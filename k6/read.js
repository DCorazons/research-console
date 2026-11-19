// Read scenario (load and stress testing): the six GET endpoints with equal shares.
//
// Each VU is one student, students[(VU id - 1) mod 1094], and calls the six endpoints
// in turn, starting at an offset given by its id, so that every endpoint receives the
// same share and the request sequence is the same for every implementation. Student
// endpoints use the student's token, the other ones the admin token.
//
//   k6 run -e BASE_URL=http://127.0.0.1:8001 -e VUS=50 -e SUMMARY=/results/x.json read.js

import exec from 'k6/execution';
import { check, sleep } from 'k6';
import { THINK, loadScenarios, meta, send, students, summary, sustainThresholds } from './lib/common.js';

const COURSE_PAGES = Math.ceil(meta.course_ids.length / 20);

export const ENDPOINTS = [
    'GET courses',
    'GET course-enrollments',
    'GET me-dashboard',
    'GET course-materials',
    'GET exam',
    'GET course-report',
];

export const options = {
    scenarios: loadScenarios('read'),
    thresholds: sustainThresholds(ENDPOINTS),
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export function read() {
    const vu = exec.vu.idInTest;
    const iteration = exec.vu.iterationInScenario;
    const student = students[(vu - 1) % students.length];
    const course = student.course_ids[iteration % student.course_ids.length];
    const exam = student.exam_ids[iteration % student.exam_ids.length];
    const endpoint = ENDPOINTS[(vu + iteration) % ENDPOINTS.length];

    let response;
    switch (endpoint) {
        case 'GET courses':
            response = send('GET', endpoint, `/api/courses?page=${(iteration % COURSE_PAGES) + 1}`, meta.admin_token);
            break;
        case 'GET course-enrollments':
            response = send('GET', endpoint, `/api/courses/${course}/enrollments`, meta.admin_token);
            break;
        case 'GET me-dashboard':
            response = send('GET', endpoint, '/api/me/dashboard', student.token);
            break;
        case 'GET course-materials':
            response = send('GET', endpoint, `/api/courses/${course}/materials`, student.token);
            break;
        case 'GET exam':
            response = send('GET', endpoint, `/api/exams/${exam}`, student.token);
            break;
        case 'GET course-report':
            response = send('GET', endpoint, `/api/reports/courses?period_id=${meta.period_id}`, meta.admin_token);
            break;
    }

    check(response, { 'status 200': (r) => r.status === 200 }, { endpoint });
    sleep(THINK);
}

export function handleSummary(data) {
    return summary(data, 'read', ENDPOINTS);
}
