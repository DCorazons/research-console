// Single request testing (table 3.6): one VU, the seven endpoints one after the other.
// Each endpoint gets WARMUP_REQUESTS warm-up requests (phase warmup, not analysed),
// then REPEATS measured requests (phase sustain), with GAP seconds between requests so
// that no two requests overlap. The POSTs use the first pairs of write-pairs.json.

import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import { DATA_DIR, meta, send, students, summary, sustainThresholds } from './lib/common.js';

const pairs = new SharedArray('write-pairs', () => JSON.parse(open(`${DATA_DIR}/write-pairs.json`)));

const WARMUP_REQUESTS = Number(__ENV.WARMUP_REQUESTS || 3);
const REPEATS = Number(__ENV.REPEATS || 10);
const GAP = Number(__ENV.GAP || 0.5);

export const ENDPOINTS = [
    'GET courses',
    'GET course-enrollments',
    'GET me-dashboard',
    'GET course-materials',
    'GET exam',
    'GET course-report',
    'POST exam-submission',
];

export const options = {
    scenarios: {
        single: { executor: 'per-vu-iterations', exec: 'single', vus: 1, iterations: 1, maxDuration: '30m' },
    },
    thresholds: sustainThresholds(ENDPOINTS),
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export function single() {
    const student = students[0];
    const course = student.course_ids[0];
    const exam = student.exam_ids[0];
    let pairIndex = 0;

    const requests = {
        'GET courses': () => send('GET', 'GET courses', '/api/courses', meta.admin_token, null, currentPhase),
        'GET course-enrollments': () => send('GET', 'GET course-enrollments', `/api/courses/${course}/enrollments`, meta.admin_token, null, currentPhase),
        'GET me-dashboard': () => send('GET', 'GET me-dashboard', '/api/me/dashboard', student.token, null, currentPhase),
        'GET course-materials': () => send('GET', 'GET course-materials', `/api/courses/${course}/materials`, student.token, null, currentPhase),
        'GET exam': () => send('GET', 'GET exam', `/api/exams/${exam}`, student.token, null, currentPhase),
        'GET course-report': () => send('GET', 'GET course-report', `/api/reports/courses?period_id=${meta.period_id}`, meta.admin_token, null, currentPhase),
        'POST exam-submission': () => {
            const pair = pairs[pairIndex++];
            return send('POST', 'POST exam-submission', `/api/exams/${pair.exam_id}/submissions`, pair.token, pair.body, currentPhase);
        },
    };

    let currentPhase = 'warmup';
    for (const endpoint of ENDPOINTS) {
        for (let i = 0; i < WARMUP_REQUESTS + REPEATS; i++) {
            currentPhase = i < WARMUP_REQUESTS ? 'warmup' : 'sustain';
            const response = requests[endpoint]();
            check(response, { 'status 2xx': (r) => r.status === 200 || r.status === 201 }, { endpoint });
            sleep(GAP);
        }
    }
}

export function handleSummary(data) {
    // Throughput is not meaningful here; the summary keeps the same shape as the load runs
    return summary(data, 'single', ENDPOINTS);
}
