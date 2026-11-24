// Read scenario on the server-rendered pages (load and stress testing).
//
// Half of the VUs are students and half are the admin, so that the six pages keep equal
// shares, as in the proposal, while each VU follows the path of a real user:
//   student: dashboard -> materials of a course -> exam page -> dashboard ...
//   admin:   course list (pages 1 to 10) -> enrollments of a course -> course report ...
// Each VU logs in once with the login form (phase "login", not analysed), then keeps
// its session cookie. Odd VUs are students (students[(VU id - 1) / 2]), even VUs the admin.
//
//   k6 run -e BASE_URL=http://127.0.0.1:8001 -e VUS=50 -e SUMMARY=/results/x.json read-web.js

import exec from 'k6/execution';
import { check, sleep } from 'k6';
import { THINK, loadScenarios, meta, students, summary, sustainThresholds } from './lib/common.js';
import { login, page } from './lib/web.js';

const COURSE_PAGES = Math.ceil(meta.course_ids.length / 20);

export const ENDPOINTS = [
    'GET courses',
    'GET course-enrollments',
    'GET course-report',
    'GET me-dashboard',
    'GET course-materials',
    'GET exam',
];

export const options = {
    scenarios: loadScenarios('read'),
    // k6 clears the cookie jar of a VU before each iteration by default; a browser
    // keeps its session cookie
    noCookiesReset: true,
    thresholds: sustainThresholds(ENDPOINTS),
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

// State of this VU, kept across iterations and scenarios
let loggedIn = false;
let step = 0;

export function read() {
    const vu = exec.vu.idInTest;
    const isStudent = vu % 2 === 1;
    const student = students[Math.floor((vu - 1) / 2) % students.length];

    if (!loggedIn) {
        login(isStudent ? student.username : meta.admin_username);
        loggedIn = true;
    }

    const round = Math.floor(step / 3);
    let response;
    let endpoint;

    if (isStudent) {
        const course = student.course_ids[round % student.course_ids.length];
        const exam = student.exam_ids[round % student.exam_ids.length];
        switch (step % 3) {
            case 0:
                endpoint = 'GET me-dashboard';
                response = page('GET', endpoint, '/research/dashboard');
                break;
            case 1:
                endpoint = 'GET course-materials';
                response = page('GET', endpoint, `/research/courses/${course}/materials`);
                break;
            default:
                endpoint = 'GET exam';
                response = page('GET', endpoint, `/research/exams/${exam}`);
        }
    } else {
        const course = meta.course_ids[(Math.floor((vu - 1) / 2) + round) % meta.course_ids.length];
        switch (step % 3) {
            case 0:
                endpoint = 'GET courses';
                response = page('GET', endpoint, `/research/courses?page=${(round % COURSE_PAGES) + 1}`);
                break;
            case 1:
                endpoint = 'GET course-enrollments';
                response = page('GET', endpoint, `/research/courses/${course}/enrollments`);
                break;
            default:
                endpoint = 'GET course-report';
                response = page('GET', endpoint, `/research/reports/courses?period_id=${meta.period_id}`);
        }
    }

    step++;
    // A redirect here would mean the session was lost and the login page was served
    check(response, { 'status 200': (r) => r.status === 200 }, { endpoint });
    if (response.status !== 200) {
        console.warn(`read: ${endpoint} answered ${response.status} for VU ${vu}`);
    }
    sleep(THINK);
}

export function handleSummary(data) {
    return summary(data, 'read-web', ENDPOINTS);
}
