// Write scenario on the server-rendered pages (load testing).
//
// Every VU is a student who takes its exams one after the other, like a real student:
//   open the exam page (the form and its CSRF token) -> post the answers -> follow the
//   redirect to the dashboard, with the think time after each page.
// A VU starts with students[VU id - 1] and, once that student has taken its 14 exams,
// logs in as the student MAX_VUS further on, so no two VUs share a student and no
// student and exam pair is posted twice in a run. The dump must be restored before
// each run, because the posted pairs stay submitted.

import exec from 'k6/execution';
import { check, sleep } from 'k6';
import { THINK, VUS, WARMUP_VUS, loadScenarios, students, summary, sustainThresholds } from './lib/common.js';
import { examForm, login, page } from './lib/web.js';

const MAX_VUS = Math.max(VUS, WARMUP_VUS);

export const ENDPOINTS = ['GET exam', 'POST exam-submission', 'GET me-dashboard'];

export const options = {
    scenarios: loadScenarios('write'),
    // k6 clears the cookie jar of a VU before each iteration by default; a browser
    // keeps its session cookie
    noCookiesReset: true,
    thresholds: sustainThresholds(ENDPOINTS),
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

// State of this VU, kept across iterations and scenarios
let studentIndex = null;
let examIndex = 0;

export function write() {
    if (studentIndex === null || examIndex >= students[studentIndex].exam_ids.length) {
        studentIndex = studentIndex === null ? exec.vu.idInTest - 1 : studentIndex + MAX_VUS;
        if (studentIndex >= students.length) {
            exec.test.abort(`write: no student left for VU ${exec.vu.idInTest}; lower VUS`);
        }
        examIndex = 0;
        login(students[studentIndex].username);
    }

    const student = students[studentIndex];
    const exam = student.exam_ids[examIndex++];

    const form = page('GET', 'GET exam', `/research/exams/${exam}`);
    check(form, { 'exam page 200': (r) => r.status === 200 }, { endpoint: 'GET exam' });
    sleep(THINK);

    const post = page('POST', 'POST exam-submission', `/research/exams/${exam}/submissions`, examForm(form.body, student.id));
    check(post, { 'submission 303': (r) => r.status === 303 }, { endpoint: 'POST exam-submission' });
    sleep(THINK);

    const dashboard = page('GET', 'GET me-dashboard', '/research/dashboard');
    check(dashboard, {
        'dashboard 200': (r) => r.status === 200,
        'score shown': (r) => (r.body || '').includes('tersimpan'),
    }, { endpoint: 'GET me-dashboard' });
    sleep(THINK);
}

export function handleSummary(data) {
    return summary(data, 'write-web', ENDPOINTS);
}
