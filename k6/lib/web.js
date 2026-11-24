// Shared parts of the page scripts (read-web.js, write-web.js, single-web.js).
//
// The pages under /research are rendered on the server with Blade, as the student pages
// of academico, and the users log in with a session cookie. k6 keeps one cookie jar per
// VU, so each VU behaves like one browser. A request ends when the whole HTML has been
// received: that is the time a user waits for the page from the server.

import http from 'k6/http';
import { BASE_URL, meta, peakMemory, phase } from './common.js';

// Pages answer 200, a form post answers with a redirect (302 or 303)
const WEB_SUCCESS = http.expectedStatuses(200, 302, 303);

/**
 * One page request. Redirects are not followed, so a form post and the page it leads
 * to are measured as two requests, each with its own endpoint tag.
 */
export function page(method, endpoint, path, form = null, phaseName = phase()) {
    const tags = { endpoint, phase: phaseName, name: endpoint };
    const params = {
        headers: { Accept: 'text/html' },
        redirects: 0,
        responseCallback: WEB_SUCCESS,
        tags,
    };

    const response = http.request(method, `${BASE_URL}${path}`, form, params);

    const memory = response.headers['X-Peak-Memory'];
    if (memory !== undefined) {
        peakMemory.add(Number(memory), tags);
    }

    return response;
}

/** The CSRF token of the first form on the page */
export function csrfToken(html) {
    const match = /name="_token" value="([^"]+)"/.exec(html || '');
    return match ? match[1] : null;
}

/**
 * Logs the VU in with the login form. These requests carry the phase "login" and stay
 * out of the analysed figures.
 */
export function login(username) {
    const form = page('GET', 'GET login', '/research/login', null, 'login');
    const response = page('POST', 'POST login', '/research/login', {
        _token: csrfToken(form.body),
        username,
        password: meta.password,
    }, 'login');

    if (response.status !== 302) {
        throw new Error(`login of ${username} failed with status ${response.status}`);
    }
}

/**
 * The form fields of an exam page, answered like a student would: every question gets
 * one option, chosen from the question and student ids so that every implementation
 * receives the same answers.
 */
export function examForm(html, studentId) {
    const form = { _token: csrfToken(html) };
    const pattern = /name="answers\[(\d+)\]\[question_id\]" value="(\d+)"/g;
    let match;
    while ((match = pattern.exec(html)) !== null) {
        const [, index, questionId] = match;
        form[`answers[${index}][question_id]`] = questionId;
        form[`answers[${index}][option]`] = 'ABCD'[(Number(questionId) + studentId) % 4];
    }
    return form;
}
