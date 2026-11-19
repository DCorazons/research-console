// Write scenario (load testing): POST /exams/{id}/submissions.
//
// Every request uses a student and exam pair that is not submitted yet, from
// write-pairs.json, with its ready-made body. The measured phase takes the pairs from
// the start of the list, by the global iteration number of the scenario, and the
// warm-up takes them from the end, so that no pair is sent twice in a run. The run
// aborts when the pairs would run out; the dump must be restored before each run.

import exec from 'k6/execution';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import { DATA_DIR, THINK, WARMUP_S, WARMUP_VUS, loadScenarios, send, summary, sustainThresholds } from './lib/common.js';

const pairs = new SharedArray('write-pairs', () => JSON.parse(open(`${DATA_DIR}/write-pairs.json`)));

// Pairs kept for the warm-up: at most one request per think time per warm-up VU, plus 20%
const WARMUP_RESERVE = Math.ceil(((WARMUP_VUS * WARMUP_S) / Math.max(THINK, 0.1)) * 1.2);

export const ENDPOINTS = ['POST exam-submission'];

export const options = {
    scenarios: loadScenarios('write'),
    thresholds: sustainThresholds(ENDPOINTS),
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export function write() {
    const index = exec.scenario.iterationInTest;
    const warmup = exec.scenario.name === 'warmup';

    if (warmup ? index >= WARMUP_RESERVE : index >= pairs.length - WARMUP_RESERVE) {
        exec.test.abort(`write pairs exhausted (${warmup ? 'warm-up' : 'measured'} index ${index}); lower VUS or THINK`);
    }

    const pair = pairs[warmup ? pairs.length - 1 - index : index];
    const response = send('POST', ENDPOINTS[0], `/api/exams/${pair.exam_id}/submissions`, pair.token, pair.body);

    check(response, { 'status 201': (r) => r.status === 201 }, { endpoint: ENDPOINTS[0] });
    sleep(THINK);
}

export function handleSummary(data) {
    return summary(data, 'write', ENDPOINTS);
}
