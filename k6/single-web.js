// Single request testing on the server-rendered pages (table 3.6): one VU, one page at
// a time. The admin pages are measured after an admin login, the student pages after a
// student login. Each page gets WARMUP_REQUESTS warm-up requests (phase warmup), then
// REPEATS measured requests (phase sustain), GAP seconds apart. Every exam post is
// preceded by its exam page (phase prep) and uses another exam of the student.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE_URL, meta, students, summary, sustainThresholds } from './lib/common.js';
import { examForm, login, page } from './lib/web.js';

const WARMUP_REQUESTS = Number(__ENV.WARMUP_REQUESTS || 3);
const REPEATS = Number(__ENV.REPEATS || 10);
const GAP = Number(__ENV.GAP || 0.5);

export const ENDPOINTS = [
    'GET courses',
    'GET course-enrollments',
    'GET course-report',
    'GET me-dashboard',
    'GET course-materials',
    'GET exam',
    'POST exam-submission',
];

export const options = {
    scenarios: {
        single: { executor: 'per-vu-iterations', exec: 'single', vus: 1, iterations: 1, maxDuration: '30m' },
    },
    thresholds: sustainThresholds(ENDPOINTS),
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

function measure(endpoint, request) {
    for (let i = 0; i < WARMUP_REQUESTS + REPEATS; i++) {
        const response = request(i < WARMUP_REQUESTS ? 'warmup' : 'sustain');
        check(response, { 'status ok': (r) => [200, 303].includes(r.status) }, { endpoint });
        sleep(GAP);
    }
}

export function single() {
    const course = meta.course_ids[0];
    login(meta.admin_username);
    measure('GET courses', (p) => page('GET', 'GET courses', '/research/courses', null, p));
    measure('GET course-enrollments', (p) => page('GET', 'GET course-enrollments', `/research/courses/${course}/enrollments`, null, p));
    measure('GET course-report', (p) => page('GET', 'GET course-report', `/research/reports/courses?period_id=${meta.period_id}`, null, p));

    // A new browser for the student: the admin session cookie is not sent any more
    http.cookieJar().clear(BASE_URL);
    const student = students[0];
    login(student.username);
    measure('GET me-dashboard', (p) => page('GET', 'GET me-dashboard', '/research/dashboard', null, p));
    measure('GET course-materials', (p) => page('GET', 'GET course-materials', `/research/courses/${student.course_ids[0]}/materials`, null, p));
    measure('GET exam', (p) => page('GET', 'GET exam', `/research/exams/${student.exam_ids[0]}`, null, p));

    let examIndex = 0;
    measure('POST exam-submission', (p) => {
        const exam = student.exam_ids[examIndex++];
        const form = page('GET', 'GET exam', `/research/exams/${exam}`, null, 'prep');
        return page('POST', 'POST exam-submission', `/research/exams/${exam}/submissions`, examForm(form.body, student.id), p);
    });
}

export function handleSummary(data) {
    return summary(data, 'single-web', ENDPOINTS);
}
