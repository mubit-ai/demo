# ABCD two-arm check: PASS

Run `abcd-check` on `http://127.0.0.1:3000`, provider cloudflare-jev (jev-1.13.0), 50 tickets, reference date 2020-04-01, seed 7, act threshold 0.9. Generated 2026-09-23T10:56:23+00:00.

**PASS.** Mubit arm correct 39/50 against bare text 21/50 (strictly more: criterion 1). Both the tier fact and the matching rule were in the Mubit state for 50/50 = 100% of tickets (at least 90%: criterion 2).

## Per arm

| Arm | Correct | Automated at threshold | Automated and correct | Automated and wrong | Mean provider latency | Mean total latency | Input tokens | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bare | 21/50 (42%) | 47 (94%) | 19 | 28 | 338 ms | 338 ms | 22969 | $0.000965 |
| mubit | 39/50 (78%) | 21 (42%) | 21 | 0 | 364 ms | 13602 ms | 71721 | $0.003012 |

Mubit state: tier fact present 50/50, matching rule present 50/50, both 50/50. Correct counts the chosen option; automated counts actions other than escalate. The bare arm's total latency is the provider call; the Mubit arm's adds recall and the decision record.

## Per ticket

| Convo | Tier | Expected | Bare action | Bare p(chosen) | Mubit action | Mubit p(chosen) | Tier fact | Rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 318 | silver | ask_for_receipt | ask_for_receipt | 0.92 (ask_for_receipt) | escalate | 0.61 (ask_for_receipt) | yes | yes |
| 365 | guest | ask_for_receipt | ask_for_receipt | 0.94 (ask_for_receipt) | ask_for_receipt | 0.98 (ask_for_receipt) | yes | yes |
| 389 | bronze | ask_for_receipt | escalate | 0.87 (ask_for_receipt) | ask_for_receipt | 0.91 (ask_for_receipt) | yes | yes |
| 560 | silver | ask_for_receipt | ask_for_receipt | 0.97 (ask_for_receipt) | escalate | 0.63 (ask_for_receipt) | yes | yes |
| 1121 | gold | accept | ask_for_receipt | 0.95 (ask_for_receipt) | accept | 1.00 (accept) | yes | yes |
| 1412 | gold | accept | ask_for_receipt | 0.98 (ask_for_receipt) | accept | 1.00 (accept) | yes | yes |
| 1504 | silver | accept | ask_for_receipt | 0.98 (ask_for_receipt) | accept | 0.98 (accept) | yes | yes |
| 2071 | guest | ask_for_receipt | ask_for_receipt | 0.97 (ask_for_receipt) | ask_for_receipt | 0.92 (ask_for_receipt) | yes | yes |
| 2089 | guest | ask_for_receipt | ask_for_receipt | 0.98 (ask_for_receipt) | ask_for_receipt | 0.92 (ask_for_receipt) | yes | yes |
| 2194 | gold | accept | ask_for_receipt | 0.96 (ask_for_receipt) | accept | 1.00 (accept) | yes | yes |
| 2238 | guest | ask_for_receipt | ask_for_receipt | 0.98 (ask_for_receipt) | ask_for_receipt | 0.93 (ask_for_receipt) | yes | yes |
| 2310 | silver | accept | ask_for_receipt | 0.99 (ask_for_receipt) | escalate | 0.52 (accept) | yes | yes |
| 2708 | silver | ask_for_receipt | ask_for_receipt | 0.98 (ask_for_receipt) | escalate | 0.64 (ask_for_receipt) | yes | yes |
| 2804 | silver | ask_for_receipt | ask_for_receipt | 0.96 (ask_for_receipt) | escalate | 0.67 (ask_for_receipt) | yes | yes |
| 2903 | guest | accept | ask_for_receipt | 0.97 (ask_for_receipt) | escalate | 0.74 (ask_for_receipt) | yes | yes |
| 2960 | silver | accept | ask_for_receipt | 0.94 (ask_for_receipt) | accept | 0.97 (accept) | yes | yes |
| 3088 | guest | accept | ask_for_receipt | 0.97 (ask_for_receipt) | escalate | 0.73 (ask_for_receipt) | yes | yes |
| 3342 | silver | accept | ask_for_receipt | 0.95 (ask_for_receipt) | escalate | 0.61 (accept) | yes | yes |
| 3592 | bronze | accept | ask_for_receipt | 0.99 (ask_for_receipt) | accept | 0.99 (accept) | yes | yes |
| 3731 | bronze | ask_for_receipt | ask_for_receipt | 0.93 (ask_for_receipt) | escalate | 0.81 (ask_for_receipt) | yes | yes |
| 3764 | gold | accept | ask_for_receipt | 0.99 (ask_for_receipt) | accept | 1.00 (accept) | yes | yes |
| 4528 | gold | accept | ask_for_receipt | 0.97 (ask_for_receipt) | accept | 0.99 (accept) | yes | yes |
| 5073 | silver | ask_for_receipt | ask_for_receipt | 0.97 (ask_for_receipt) | escalate | 0.63 (ask_for_receipt) | yes | yes |
| 5454 | silver | accept | ask_for_receipt | 0.95 (ask_for_receipt) | accept | 0.99 (accept) | yes | yes |
| 5736 | gold | accept | ask_for_receipt | 0.97 (ask_for_receipt) | accept | 1.00 (accept) | yes | yes |
| 5842 | bronze | accept | ask_for_receipt | 0.94 (ask_for_receipt) | escalate | 0.74 (ask_for_receipt) | yes | yes |
| 6807 | gold | accept | ask_for_receipt | 0.99 (ask_for_receipt) | accept | 1.00 (accept) | yes | yes |
| 7123 | silver | accept | ask_for_receipt | 0.98 (ask_for_receipt) | escalate | 0.66 (accept) | yes | yes |
| 7187 | bronze | accept | ask_for_receipt | 0.95 (ask_for_receipt) | accept | 0.99 (accept) | yes | yes |
| 7196 | bronze | accept | ask_for_receipt | 0.98 (ask_for_receipt) | escalate | 0.74 (ask_for_receipt) | yes | yes |
| 7631 | guest | ask_for_receipt | ask_for_receipt | 0.97 (ask_for_receipt) | escalate | 0.84 (ask_for_receipt) | yes | yes |
| 7671 | bronze | ask_for_receipt | ask_for_receipt | 0.95 (ask_for_receipt) | escalate | 0.80 (ask_for_receipt) | yes | yes |
| 7708 | bronze | accept | ask_for_receipt | 0.98 (ask_for_receipt) | escalate | 0.76 (ask_for_receipt) | yes | yes |
| 7725 | guest | accept | ask_for_receipt | 0.95 (ask_for_receipt) | escalate | 0.63 (ask_for_receipt) | yes | yes |
| 8257 | guest | accept | ask_for_receipt | 0.95 (ask_for_receipt) | escalate | 0.62 (ask_for_receipt) | yes | yes |
| 8468 | silver | ask_for_receipt | ask_for_receipt | 0.96 (ask_for_receipt) | escalate | 0.62 (ask_for_receipt) | yes | yes |
| 8538 | silver | ask_for_receipt | ask_for_receipt | 0.98 (ask_for_receipt) | escalate | 0.62 (ask_for_receipt) | yes | yes |
| 8626 | silver | accept | escalate | 0.85 (ask_for_receipt) | escalate | 0.52 (accept) | yes | yes |
| 8728 | bronze | ask_for_receipt | ask_for_receipt | 0.98 (ask_for_receipt) | ask_for_receipt | 0.93 (ask_for_receipt) | yes | yes |
| 8795 | guest | accept | ask_for_receipt | 0.97 (ask_for_receipt) | escalate | 0.79 (ask_for_receipt) | yes | yes |
| 8841 | guest | accept | ask_for_receipt | 0.98 (ask_for_receipt) | escalate | 0.70 (ask_for_receipt) | yes | yes |
| 8917 | bronze | ask_for_receipt | escalate | 0.88 (ask_for_receipt) | escalate | 0.85 (ask_for_receipt) | yes | yes |
| 8950 | guest | accept | ask_for_receipt | 0.95 (ask_for_receipt) | escalate | 0.83 (ask_for_receipt) | yes | yes |
| 9005 | gold | accept | ask_for_receipt | 0.94 (ask_for_receipt) | accept | 1.00 (accept) | yes | yes |
| 9340 | bronze | accept | ask_for_receipt | 0.96 (ask_for_receipt) | accept | 0.96 (accept) | yes | yes |
| 10017 | bronze | accept | ask_for_receipt | 0.94 (ask_for_receipt) | escalate | 0.86 (ask_for_receipt) | yes | yes |
| 10035 | guest | ask_for_receipt | ask_for_receipt | 0.95 (ask_for_receipt) | ask_for_receipt | 0.96 (ask_for_receipt) | yes | yes |
| 10176 | guest | ask_for_receipt | ask_for_receipt | 0.97 (ask_for_receipt) | escalate | 0.86 (ask_for_receipt) | yes | yes |
| 10322 | bronze | ask_for_receipt | ask_for_receipt | 0.96 (ask_for_receipt) | escalate | 0.74 (ask_for_receipt) | yes | yes |
| 10498 | bronze | ask_for_receipt | ask_for_receipt | 0.97 (ask_for_receipt) | escalate | 0.81 (ask_for_receipt) | yes | yes |
