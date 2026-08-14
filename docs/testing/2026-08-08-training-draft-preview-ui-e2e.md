# Training draft preview UI - E2E acceptance

- Date: 2026-08-08
- Scope: `web/templates/training.html` and `tests/test_web.py`
- Boundary: independent acceptance only; no business-code or test-assertion changes.

## Acceptance plan

1. Verify the draft preview presents the four information layers in order: rationale, current week, next week, and later cycle.
2. Verify the detailed evidence/uncertainty content is collapsed by default.
3. Verify the conditional safety notice and the review-to-confirm continuation action remain present.
4. Run the focused Web/API regression suite.
5. If a local server is available, verify `GET /healthz` at `127.0.0.1:8080`.

## Evidence and results

| Check | Result | Evidence |
| --- | --- | --- |
| Four-layer preview | Passed | `draftMarkup()` renders `为什么这样安排`, `当前周安排`, `下一周安排`, and `后续周期`; `tests/test_web.py::test_training_page_and_api_complete_confirmed_adjustment_flow` asserts all four labels. |
| Current/next week detail | Passed | The renderer projects `first_four_weeks[0]` and `[1]` through `workoutMarkup()`, with `weekly_pattern` retained for the first week. |
| Collapsed basis | Passed | The evidence/uncertainty area uses `<details class="plan-section detail-block">` with summary `查看依据与不确定性`. |
| Safety notice | Passed | When `review.safety_hold` exists, `safeNotice` renders `需要确认的安全提示` and its server-supplied reason/message. |
| Continue confirmation | Passed | `renderReviewStep()` contains the `#reviewNext` primary button labelled `继续确认`, which transitions the wizard to `confirm`. |
| Focused regression | Passed | `pytest -q tests/test_web.py` -> `54 passed in 4.91s`. |
| Live health check | Blocked (environment) | `curl --silent --show-error --fail --max-time 3 http://127.0.0.1:8080/healthz` -> connection refused; no local service was listening. |

## Failure layer and recommendation

The sole unavailable check fails at the local runtime/environment layer (TCP connection establishment), before HTTP routing, page rendering, console, or network assertions. Do not modify the preview UI. Start the local Web service on port 8080, then rerun the health probe and perform a browser journey with a seeded pending draft to collect interactive screenshot/console/network evidence.
