// Shared parts of the k6 scripts (thesis proposal, section 3.2.4 and table 3.6).
//
// Environment variables:
//   BASE_URL     application stack, for example http://127.0.0.1:8001
//   DATA_DIR     folder of meta.json, students.json, write-pairs.json
//                (php artisan research:export-k6-data), default /data
//   VUS          target VUs of the measured phase (load and stress)
//   WARMUP_VUS   VUs of the warm-up phase, default 5
//   WARMUP       warm-up duration, default 60s; RAMP default 30s; SUSTAIN default 60s
//   THINK        think time after each request in seconds, default 1
//   SUMMARY      path of the JSON summary written at the end, default summary.json
//   RUN_ID       free label stored in the summary (block, implementation, level)

import exec from 'k6/execution';
import http from 'k6/http';
import { SharedArray } from 'k6/data';
import { Trend } from 'k6/metrics';

export const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8001';
export const DATA_DIR = __ENV.DATA_DIR || '/data';
export const THINK = Number(__ENV.THINK || 1);

export const WARMUP_S = seconds(__ENV.WARMUP || '60s');
export const RAMP_S = seconds(__ENV.RAMP || '30s');
export const SUSTAIN_S = seconds(__ENV.SUSTAIN || '60s');
export const VUS = Number(__ENV.VUS || 50);
export const WARMUP_VUS = Number(__ENV.WARMUP_VUS || 5);

export const meta = JSON.parse(open(`${DATA_DIR}/meta.json`));
export const students = new SharedArray('students', () => JSON.parse(open(`${DATA_DIR}/students.json`)));

/** Peak memory of PHP for each request, from the X-Peak-Memory header, in bytes */
export const peakMemory = new Trend('peak_memory', false);

// Only 200 and 201 are successes; 4xx and 5xx count in http_req_failed
http.setResponseCallback(http.expectedStatuses(200, 201));

export function seconds(duration) {
    const match = /^(\d+)(s|m)?$/.exec(String(duration));
    if (!match) {
        throw new Error(`Unsupported duration ${duration}, use for example 60s or 2m`);
    }
    return Number(match[1]) * (match[2] === 'm' ? 60 : 1);
}

/**
 * The phase of the current request: warmup (warm-up scenario), rampup and sustain
 * (measured scenario, by time since its start), cooldown (iterations that finish
 * after the sustain window). Only sustain is analysed.
 */
export function phase() {
    if (exec.scenario.name === 'warmup') {
        return 'warmup';
    }
    const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
    if (elapsed < RAMP_S) {
        return 'rampup';
    }
    return elapsed < RAMP_S + SUSTAIN_S ? 'sustain' : 'cooldown';
}

/** Warm-up at low load, then ramp-up and sustain at VUS (table 3.6) */
export function loadScenarios(exec) {
    return {
        warmup: {
            executor: 'constant-vus',
            exec,
            vus: WARMUP_VUS,
            duration: `${WARMUP_S}s`,
            gracefulStop: '0s',
        },
        measured: {
            executor: 'ramping-vus',
            exec,
            startTime: `${WARMUP_S}s`,
            startVUs: 0,
            stages: [
                { duration: `${RAMP_S}s`, target: VUS },
                { duration: `${SUSTAIN_S}s`, target: VUS },
            ],
            gracefulRampDown: '0s',
            gracefulStop: '0s',
        },
    };
}

/** Sends one request, tags it with the endpoint and phase, and records peak memory */
export function send(method, endpoint, path, token, body = null, phaseName = phase()) {
    const tags = { endpoint, phase: phaseName, name: endpoint };
    const params = { headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' }, tags };
    if (body !== null) {
        params.headers['Content-Type'] = 'application/json';
    }

    const response = http.request(method, `${BASE_URL}${path}`, body, params);

    const memory = response.headers['X-Peak-Memory'];
    if (memory !== undefined) {
        peakMemory.add(Number(memory), tags);
    }

    return response;
}

/**
 * Thresholds that never fail. They only make k6 compute the sustain sub-metrics,
 * overall and per endpoint, so that they appear in the JSON summary.
 */
export function sustainThresholds(endpoints) {
    const thresholds = {};
    const selectors = ['phase:sustain', ...endpoints.map((endpoint) => `phase:sustain,endpoint:${endpoint}`)];
    for (const selector of selectors) {
        thresholds[`http_req_duration{${selector}}`] = ['max>=0'];
        thresholds[`http_req_failed{${selector}}`] = ['rate>=0'];
        thresholds[`http_reqs{${selector}}`] = ['count>=0'];
        thresholds[`peak_memory{${selector}}`] = ['max>=0'];
    }
    return thresholds;
}

/** Writes the JSON summary for the research console and prints the sustain figures */
export function summary(data, scenario, endpoints) {
    const sustain = (name, endpoint) => {
        const key = endpoint ? `${name}{phase:sustain,endpoint:${endpoint}}` : `${name}{phase:sustain}`;
        return data.metrics[key] ? data.metrics[key].values : {};
    };
    const row = (endpoint) => {
        const requests = sustain('http_reqs', endpoint).count || 0;
        const failed = sustain('http_req_failed', endpoint).rate || 0;
        const duration = sustain('http_req_duration', endpoint);
        return {
            endpoint: endpoint || 'all',
            requests,
            throughput: (requests * (1 - failed)) / SUSTAIN_S,
            error_rate: failed,
            avg_ms: duration.avg,
            med_ms: duration.med,
            p95_ms: duration['p(95)'],
            max_ms: duration.max,
            peak_memory_avg: sustain('peak_memory', endpoint).avg,
            peak_memory_max: sustain('peak_memory', endpoint).max,
        };
    };

    const rows = [row(null), ...endpoints.map(row)];
    const result = {
        scenario,
        run_id: __ENV.RUN_ID || null,
        base_url: BASE_URL,
        vus: VUS,
        warmup_vus: WARMUP_VUS,
        warmup_s: WARMUP_S,
        ramp_s: RAMP_S,
        sustain_s: SUSTAIN_S,
        think_s: THINK,
        sustain: rows,
        metrics: data.metrics,
    };

    const line = (r) => `${r.endpoint.padEnd(24)} ${String(r.requests).padStart(7)} req ${r.throughput.toFixed(2).padStart(8)} req/s `
        + `err ${(r.error_rate * 100).toFixed(2).padStart(6)}% p95 ${(r.p95_ms || 0).toFixed(1).padStart(8)} ms `
        + `mem ${((r.peak_memory_avg || 0) / 1048576).toFixed(2)} MiB`;

    const title = scenario.startsWith('single')
        ? 'single requests (measured requests only, 1 VU)'
        : `${scenario} sustain (${SUSTAIN_S}s, ${VUS} VU, think ${THINK}s)`;

    return {
        [__ENV.SUMMARY || 'summary.json']: JSON.stringify(result, null, 2),
        stdout: `\n${title}\n${rows.map(line).join('\n')}\n`,
    };
}
