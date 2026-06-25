# Tasks: Voice-Live Weather Agent

**Input**: Design documents from `/specs/001-voice-live-agent/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included because plan.md specifies pytest validation for provider behavior, configuration validation, protocol message handling, and session-state edge cases.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Create `hosted-agents/weather-forecast/` directory structure with `agent/`, `client/`, and `tests/` subdirectories
- [x] T002 Create `hosted-agents/weather-forecast/requirements.txt` with runtime and test dependencies from plan.md
- [x] T003 [P] Create `hosted-agents/weather-forecast/.env.example` from `specs/001-voice-live-agent/contracts/configuration.md`
- [x] T004 [P] Create package markers `hosted-agents/weather-forecast/agent/__init__.py` and `hosted-agents/weather-forecast/tests/__init__.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Implement `Settings` loading and validation in `hosted-agents/weather-forecast/agent/config.py`
- [x] T006 [P] Implement `VoiceSession` state model and in-memory session store in `hosted-agents/weather-forecast/agent/session_state.py`
- [x] T007 [P] Implement WebSocket message dataclasses and JSON parsing helpers in `hosted-agents/weather-forecast/agent/protocol.py`
- [x] T008 [P] Implement pytest coverage for configuration defaults and invalid provider handling in `hosted-agents/weather-forecast/tests/test_config.py`
- [x] T009 [P] Implement pytest coverage for session state updates without transcript/audio persistence in `hosted-agents/weather-forecast/tests/test_session_state.py`
- [x] T010 [P] Implement pytest coverage for WebSocket message parsing and 1 MB frame limit helpers in `hosted-agents/weather-forecast/tests/test_protocol.py`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Ask for a Weather Forecast by Voice (Priority: P1) MVP

**Goal**: User can start the sample conversation and receive a concise spoken/weather-style Japanese response for a city-specific forecast question.

**Independent Test**: Start the local sample, send "今日の東京の天気は？", and confirm a Japanese forecast response for Tokyo is returned.

### Tests for User Story 1

- [x] T011 [P] [US1] Add mock provider tests for Tokyo, Osaka, Kyoto, Sapporo, and Fukuoka forecasts in `hosted-agents/weather-forecast/tests/test_weather.py`
- [x] T012 [P] [US1] Add protocol handler test for a `weather.request` text frame returning `weather.response` in `hosted-agents/weather-forecast/tests/test_protocol.py`

### Implementation for User Story 1

- [x] T013 [P] [US1] Implement `WeatherRequest` and `ForecastResponse` models in `hosted-agents/weather-forecast/agent/weather.py`
- [x] T014 [US1] Implement mock weather provider responses for supported cities in `hosted-agents/weather-forecast/agent/weather.py`
- [x] T015 [US1] Implement weather request parsing for Japanese city/day phrases in `hosted-agents/weather-forecast/agent/weather.py`
- [x] T016 [US1] Implement local hosted-agent-compatible WebSocket handler in `hosted-agents/weather-forecast/agent/app.py`
- [x] T017 [US1] Implement text WebSocket validation client in `hosted-agents/weather-forecast/client/text_client.py`
- [x] T018 [US1] Wire `weather.request` to `weather.response` through session state in `hosted-agents/weather-forecast/agent/app.py`

**Checkpoint**: User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Recover from Missing or Ambiguous Location (Priority: P2)

**Goal**: User receives a natural Japanese clarification when asking for weather without a location, then gets a forecast after providing the city.

**Independent Test**: Send "今日の天気は？" and confirm a `weather.clarification`; then send "東京" and confirm a Tokyo forecast response.

### Tests for User Story 2

- [x] T019 [P] [US2] Add missing-location parser tests in `hosted-agents/weather-forecast/tests/test_weather.py`
- [x] T020 [P] [US2] Add follow-up location resolution tests in `hosted-agents/weather-forecast/tests/test_session_state.py`
- [x] T021 [P] [US2] Add protocol handler test for `weather.clarification` then follow-up response in `hosted-agents/weather-forecast/tests/test_protocol.py`

### Implementation for User Story 2

- [x] T022 [US2] Implement missing-location detection and Japanese clarification prompt in `hosted-agents/weather-forecast/agent/weather.py`
- [x] T023 [US2] Implement pending weather request continuation after a user provides a location in `hosted-agents/weather-forecast/agent/session_state.py`
- [x] T024 [US2] Integrate clarification and follow-up handling in `hosted-agents/weather-forecast/agent/app.py`

**Checkpoint**: User Stories 1 and 2 should both work independently

---

## Phase 5: User Story 3 - Explain Sample Setup Clearly (Priority: P3)

**Goal**: Developer can configure, run, and understand the sample from a clean checkout, including whether demo or live weather data is used.

**Independent Test**: Follow `hosted-agents/weather-forecast/README.md` from a clean checkout and reach a working local text WebSocket weather response.

### Tests for User Story 3

- [x] T025 [P] [US3] Add missing Foundry configuration validation tests in `hosted-agents/weather-forecast/tests/test_config.py`
- [x] T026 [P] [US3] Add README command smoke-test notes to `hosted-agents/weather-forecast/tests/test_readme_contract.py`

### Implementation for User Story 3

- [x] T027 [US3] Write setup and validation guide in `hosted-agents/weather-forecast/README.md`
- [x] T028 [US3] Document demo-vs-live weather provider behavior in `hosted-agents/weather-forecast/README.md`
- [x] T029 [US3] Add startup validation messages for missing hosted deployment values in `hosted-agents/weather-forecast/agent/config.py`
- [x] T030 [US3] Add sample commands from `specs/001-voice-live-agent/quickstart.md` to `hosted-agents/weather-forecast/README.md`

**Checkpoint**: All user stories should now be independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T031 [P] Add optional JMA provider skeleton with timeout/error-safe fallback in `hosted-agents/weather-forecast/agent/weather.py`
- [x] T032 [P] Add Dockerfile for hosted-agent container deployment in `hosted-agents/weather-forecast/Dockerfile`
- [x] T033 [P] Add hosted `invocations_ws` deployment notes in `hosted-agents/weather-forecast/README.md`
- [x] T034 Run `pytest hosted-agents/weather-forecast/tests` and fix failures
- [x] T035 Run quickstart validation commands from `specs/001-voice-live-agent/quickstart.md`
- [x] T036 Update root `README.md` sample list to link to `hosted-agents/weather-forecast/README.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - US1 is the MVP and should be completed first
  - US2 can begin after foundational tasks but integrates most naturally after US1 request/response flow exists
  - US3 can begin after setup paths stabilize
- **Polish (Phase 6)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational - no dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational - depends on US1 only for final app integration path
- **User Story 3 (P3)**: Can start after Foundational - depends on finalized file paths and commands

### Parallel Opportunities

- T003 and T004 can run in parallel after T001
- T006, T007, T008, T009, and T010 can run in parallel once T005 file shape is known
- T011 and T012 can run in parallel before US1 implementation
- T019, T020, and T021 can run in parallel before US2 implementation
- T025 and T026 can run in parallel before US3 documentation/config completion
- T031, T032, and T033 can run in parallel during polish

---

## Parallel Example: User Story 1

```bash
Task: "Add mock provider tests for Tokyo, Osaka, Kyoto, Sapporo, and Fukuoka forecasts in hosted-agents/weather-forecast/tests/test_weather.py"
Task: "Add protocol handler test for a weather.request text frame returning weather.response in hosted-agents/weather-forecast/tests/test_protocol.py"
Task: "Implement WeatherRequest and ForecastResponse models in hosted-agents/weather-forecast/agent/weather.py"
```

## Parallel Example: User Story 2

```bash
Task: "Add missing-location parser tests in hosted-agents/weather-forecast/tests/test_weather.py"
Task: "Add follow-up location resolution tests in hosted-agents/weather-forecast/tests/test_session_state.py"
Task: "Add protocol handler test for weather.clarification then follow-up response in hosted-agents/weather-forecast/tests/test_protocol.py"
```

## Parallel Example: User Story 3

```bash
Task: "Add missing Foundry configuration validation tests in hosted-agents/weather-forecast/tests/test_config.py"
Task: "Add README command smoke-test notes to hosted-agents/weather-forecast/tests/test_readme_contract.py"
Task: "Write setup and validation guide in hosted-agents/weather-forecast/README.md"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. Stop and validate local text WebSocket weather response for "今日の東京の天気は？"
5. Demo MVP before adding clarification and deployment documentation

### Incremental Delivery

1. Setup + Foundational -> Foundation ready
2. US1 -> Japanese city weather response MVP
3. US2 -> Missing-location clarification and follow-up flow
4. US3 -> Clean setup documentation and configuration validation
5. Polish -> Optional JMA provider, Dockerfile, hosted deployment notes

### Team Strategy

1. Richard reviews architecture and protocol boundaries.
2. Gilfoyle owns `agent/` implementation and hosted-agent deployment shape.
3. Dinesh owns `client/text_client.py` and any future browser client.
4. Big Head owns pytest coverage and quickstart validation.
5. Jared keeps README/setup steps aligned with Spec Kit artifacts.

---

## Notes

- [P] tasks = different files, no dependencies
- [US1], [US2], [US3] labels map tasks to specific user stories
- Each user story is independently completable and testable
- Commit after each task or logical group
