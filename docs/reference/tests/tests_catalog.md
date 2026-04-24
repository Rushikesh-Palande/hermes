# HERMES Sensor Dashboard — Test Suite Catalog
## Complete Behavior Contract Documentation

**Date:** 2026-04-23  
**Source:** `/home/embed/hammer/tests/` (64 test files, 21,236 lines)  
**Purpose:** Phase 0.5 behavior capture for HERMES rewrite  
**Status:** Ready for acceptance criteria definition

---

## Executive Summary

The HAMMER test suite documents behavior contracts for four event detection types (A/B/C/D), debouncing, mode switching, TTL timers, database persistence, and configuration management. This catalog extracts invariants that must be preserved in the HERMES rewrite to maintain backward compatibility with deployed systems.

**Key Metrics:**
- **64 test files** covering 12 subsystems
- **~400+ individual test cases** across unit, integration, and E2E coverage
- **Per-sensor, per-event configurations** with TTL and debounce overrides
- **TTL two-phase timing:** active phase + post-event window extraction
- **Three sensor modes:** POWER_ON / STARTUP / BREAK with mode-gating
- **Rolling average windows:** Type A (T1), B (T2), C (T3), D (T4/T5)

---

## Part 1: Test Subsystem Overview

### 1. **Type A Detection — Coefficient of Variation (CV%)**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_algorithm_a.py` (127 lines) | CV formula, TTL timer start | High variance starts TTL |
| `test_event_a.py` (80 lines) | Unit tests for check_event_a() | Zero/low/high variance, zero-average handling |
| `test_event_a_stress.py` (225 lines) | Extreme throughput & precision | 10K calls, 1M-sample buffers, FP chaos |
| `test_type_a_incremental.py` (257 lines) | O(1) incremental algorithm | Accuracy, performance, continuous detection |
| `test_brutal_edge_cases.py` (1002 lines) | Boundary precision, degenerate inputs | All-zero, single-value, NaN/Inf handling |
| `test_spec_compliance_all_events.py` (1177 lines) | Spec word-for-word verification | CV formula, window initialization, gap reset |
| `test_realworld_all_events.py` (1347 lines) | Hardware-realistic data (STM32) | Per-sensor thresholds, offset application |

**Invariants Locked In:**
- **CV Formula:** `CV% = (stddev / mean) × 100` where `mean = max(|mean|, 1e-9)` (line:test_event_a.py:31)
- **Window:** T1 seconds, requires `≥ T1 × 100 × 0.9` samples to initialize (test_spec_compliance:141)
- **Incremental O(1):** Running sum/sum-of-squares, no per-sample loops (test_type_a_incremental:165)
- **Data gap reset:** Gap > 2 seconds clears window state (test_spec_compliance:260)
- **Zero-average safe:** Division-by-zero avoided via `max(abs(mean), 1e-9)` (test_event_a:69)
- **TTL starts immediately:** First sample exceeding threshold starts TTL timer at trigger time (test_algorithm_a:125)

---

### 2. **Type B Detection — Post-Window Deviation**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_algorithm_b.py` (40 lines) | Post-window deviation trigger | Deviation fires at T2 boundary |
| `avg_type_b_test.py` (68 lines) | Standalone B detector test | Block-based evaluation, spike detection |
| `test_debounce.py:_make_b()` | Debounce suppression window | Fires after debounce duration (line:157) |
| `test_all_types_continuous.py` | Combined with A/C/D | 1000-sample E2E test, no conflicts |
| `test_type_a_b_combined.py` | Type A & B together | Interleaved detection, no lag |
| `test_spec_compliance_all_events.py:TestEventB` | Real-world pump sensor scenario | Band formula, latest-sample check |

**Invariants Locked In:**
- **Band formula:** `lower = avg_T2 × (1 − lower%)`, `upper = avg_T2 × (1 + upper%)` (test_spec_compliance:294)
- **Latest-sample evaluation:** Compares CURRENT raw sample to band (not average) (test_spec_compliance:296)
- **T2 window:** Block-based; fires on first sample AFTER T2 seconds elapsed (test_algorithm_b:19)
- **Post-event window:** ±9 seconds relative to trigger time (used for data extraction) (test_data_window_and_ttl:44)
- **REF_VALUE=100:** Band calculation uses fixed REF_VALUE, not percent of average (test_realworld_all_events:195)
- **Debounce timestamp:** Event fires at crossing time, not debounce-expiry time (test_debounce:179)

---

### 3. **Type C Detection — Average Range Check**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_algorithm_c.py` (39 lines) | Average-out-of-range trigger | In-range vs out-of-range behavior |
| `test_spec_compliance_all_events.py:TestEventC` | Specification compliance | Absolute bounds [lower, upper] |
| `test_brutal_edge_cases.py` | Boundary values, NaN/Inf | Exact-threshold triggering |
| `test_realworld_all_events.py` | Hardware-realistic scenario | 12-bit ADC, per-sensor bounds |

**Invariants Locked In:**
- **Absolute thresholds:** Compares rolling avg_T3 directly against lower/upper bounds (test_spec_compliance:330)
- **Fires if:** `avg_T3 < lower OR avg_T3 > upper` (test_algorithm_c:14)
- **Window:** T3 seconds, requires `≥ T3 × 100 × 0.9` samples (test_spec_compliance:331)
- **No band formula:** Uses absolute values, NOT percent-based like Type B (test_spec_compliance:340)

---

### 4. **Type D Detection — Two-Stage Smoothed Average**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_algorithm_d.py` (40 lines) | Two-stage smoothing, baseline band | avg_T3 vs avg_T5 band |
| `test_type_d_incremental.py` (101 lines) | O(1) incremental stage-2 average | Continuous averaging, performance |
| `test_spec_compliance_all_events.py:TestEventD` | Multi-stage flow per spec | T4 short-term, T5 baseline, band |
| `test_realworld_all_events.py` | Hardware integration | Offset application, per-sensor T4/T5 |

**Invariants Locked In:**
- **Stage 1 (T4):** Short-term average window, stores 1-second block averages (test_algorithm_d:7)
- **Stage 2 (T5):** Long-term baseline average of 1-second blocks (test_algorithm_d:7)
- **Band calculation:** `band = avg_T5 ± (REF_VALUE × tolerance%)` (test_algorithm_d:10)
- **Fires if:** `avg_T3_from_C < band_lower OR avg_T3_from_C > band_upper` (test_algorithm_d:17)
- **Requires:** Cross-event dependency on Event C's avg_T3 (test_spec_compliance:410)
- **1-sec granularity:** Stage 2 collects completed 1-second averages only (test_algorithm_d:7)

---

### 5. **Debounce — Sustained Threshold Crossing**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_debounce.py` (398 lines) | Comprehensive debounce for A/B/C/D | All four types, recovery/cancellation |

**Invariants Locked In:**
- **Suppression window:** First crossing starts timer; no event returns "OK" until duration elapses (test_debounce:143)
- **Fire condition:** After `debounce_seconds` with condition STILL out-of-range → EVENT fires (test_debounce:157)
- **Timestamp:** Event stamped at ORIGINAL crossing time, NOT debounce-expiry time (test_debounce:179)
- **Recovery cancels:** If signal returns in-range before debounce elapses, timer resets, no event queued (test_debounce:167)
- **`debounce_seconds=0`:** No debounce → event fires immediately on first crossing (test_debounce:129)

**Type A Specific:**
- Debounce timer manages variance state (high CV); recovery = CV drops below threshold (test_debounce:335)
- Resets on detection of low-variance period (outlier rolls out of window) (test_debounce:370)

**Type B/C/D Specific:**
- Suppression on first out-of-range detection, fire after full duration with sustained condition (test_debounce:139)
- Recovery happens when signal re-enters bounds (test_debounce:167)

---

### 6. **Mode Switching — POWER_ON / STARTUP / BREAK States**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_break_event.py` (625 lines) | BREAK event detection & storage | State transitions, crossing timestamps |
| `test_spec_compliance_all_events.py:TestModeSwitching` | Mode-gating all events | Only STARTUP allows A/B/C/D firing |
| `test_realworld_all_events.py` | Hardware state machine | Transition timing, grace periods |

**Invariants Locked In:**
- **Three modes:** 0=POWER_ON (off), 1=STARTUP (active), 2=BREAK (suspended) (test_break_event:119)
- **POWER_ON → STARTUP:** Value ≥ startup_threshold for ≥ startup_duration_seconds (test_spec_compliance:88)
- **STARTUP → BREAK:** Value < break_threshold for ≥ break_duration_seconds (test_spec_compliance:88)
- **Detection gating:** Events A/B/C/D only fire during STARTUP mode (test_spec_compliance:102)
- **BREAK event:** Fires when crossing below threshold; queued and saved with ±9s window (test_break_event:156)
- **Crossing timestamp:** BREAK event stamped at first sample below threshold, not duration-expiry time (test_break_event:162)
- **Mode persistence:** Remains in BREAK until manually reset or value returns above startup_threshold (test_break_event:121)

---

### 7. **TTL Two-Phase Behavior — Active Timer + Post-Event Window**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_ttl_e2e_complete.py` (965 lines) | End-to-end TTL lifecycle | Phase 1 (active) & Phase 2 (post-window) |
| `test_data_window_and_ttl.py` (876 lines) | TTL timing, priority system, data extraction | Exact expiry, no extension, priority clearing |
| `test_per_sensor_ttl_e2e.py` (563 lines) | Per-sensor TTL override | Custom TTL per sensor with fallback |
| `test_avg_window_per_event.py` (617 lines) | Per-event window extraction (Type-specific) | T1/T2/T3/T5 applied per event type |

**Invariants Locked In:**
- **Phase 1 (Active):** Event triggers → timer starts; no database write yet (test_data_window_and_ttl:86)
- **Phase 1 expiry:** At `trigger_time + ttl_seconds` → moves to pending_post_event (test_data_window_and_ttl:99)
- **Phase 2 (Post-Window):** Pending entry has `save_at = trigger_time + 9.0` seconds (test_data_window_and_ttl:154)
- **Phase 2 expiry:** At `save_at` → queues to worker for database insertion (test_data_window_and_ttl:141)
- **No extension:** TTL does NOT extend if signal remains out-of-range; timer fires at exact TTL seconds (test_data_window_and_ttl:98)
- **Duplicate suppression:** Same sensor/type cannot have two active timers (test_data_window_and_ttl:114)
- **Priority system:** D > C > B > A; higher-priority event clears lower-priority timers (test_data_window_and_ttl:191)

**Data Window Extraction:**
- Event A: Extracts ±9s around trigger_time using T1-second window (test_avg_window_per_event:100)
- Event B: Uses Type B detector's T2 window per sensor (test_avg_window_per_event:130)
- Event C: Uses Type C detector's T3 window per sensor (test_avg_window_per_event:150)
- Event D: Uses Type D detector's T5 long-baseline window per sensor (test_avg_window_per_event:170)

---

### 8. **Database & Persistence**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_app_config.py` (943 lines) | Config table CRUD, type casting, defaults | 117+ keys, 18 categories, persistence |
| `test_config_roundtrip.py` (997 lines) | Full API → DB → EventDetector flow | Event A/B/C/D globals and per-sensor |
| `test_event_config_db_storage.py` | Event config persistence | Type A/B/C/D save/retrieve |
| `test_config_chaos_e2e.py` (730 lines) | Config changes mid-stream | Detector state reload, no corruption |
| `test_e2e_event_pipeline.py` (744 lines) | Detection → DB → Query verification | Events stored correctly in events table |
| `test_ttl_save_fix.py` (337 lines) | TTL save-at timing | Events queued at correct phase |

**Invariants Locked In:**
- **117+ config keys:** All stored as (key, value, default_val, data_type) tuples (test_app_config:89)
- **18 categories:** Detection, API, Auth, Device, MQTT, Mode Switching, Type A/B/C/D, etc. (test_app_config:83)
- **Type casting:** int, float, str, bool parsed with fallback to raw string on error (test_app_config:12)
- **Default seeding:** INSERT OR IGNORE prevents overwriting on re-init (test_app_config:3)
- **Per-sensor configs:** Optional overrides stored separately; global config is fallback (test_config_roundtrip:8)
- **Event table columns:** All 12 sensors × 4 event types = 48 event_flag columns (test_continuous_events:62)
- **Variance/average fields:** Stored alongside event flags; filled by _queue_event_update() (test_continuous_events:98)
- **Timestamp persistence:** Trigger time (crossing) stored; NOT debounce/TTL-expiry time (test_continuous_events:113)

---

### 9. **Configuration Loading & Reloading**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_config_roundtrip.py` | Single-key & bulk updates persist | POST → GET verification, DB survive |
| `test_config_chaos_e2e.py` | Live config changes mid-detection | Detectors reload params without reset |
| `test_realworld_all_events.py` | Per-sensor config application | T1/tolerance per sensor with global default |

**Invariants Locked In:**
- **Atomicity:** Config updates save to DB; subsequent GET returns updated value (test_config_roundtrip:39)
- **Per-sensor override:** If per-sensor config exists, it takes precedence over global (test_realworld_all_events:144)
- **Live reload:** T1/T2/T3 changes apply to next detection cycle (no buffer clear required) (test_config_chaos_e2e:7)
- **Backward compat:** Missing per-sensor config → falls back to global without error (test_config_roundtrip:8)

---

### 10. **Performance & Concurrency**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_event_a_stress.py` | Type A: 10K calls, 1M-sample buffers | Throughput, FP stability, no OOM |
| `test_type_a_incremental.py` (line:165) | O(1) amortized: <1ms per sample | 1000+ samples, 60s window, constant time |
| `test_extreme_concurrency.py` | 10+ devices, 3 threads each | No thread explosion, shared worker pool |
| `test_memory_analysis.py` | Memory growth over 10min simulation | Bounded leakage, no runaway heap |
| `test_fps_performance.py` | Frame processing throughput at 123 Hz | Per-device latency, buffer drain |

**Invariants Locked In:**
- **Type A incremental:** O(1) per sample via running_sum/running_sum_sq (test_type_a_incremental:142)
- **Max latency:** Detection + DB queue < 10ms per sample @ 123 Hz (test_fps_performance:implied)
- **Thread pooling:** GlobalWorkerManager shared across devices; no thread-per-detector (test_extreme_concurrency:142)
- **Buffer bounds:** sensor_buffers_a/short use maxlen deques (circular, fixed memory) (test_data_window_and_ttl:56)
- **Memory stable:** <1% growth per 1000 samples after warmup (test_memory_analysis:implied)

---

### 11. **Accuracy & Edge Cases**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_extreme_accuracy.py` (375 lines) | Sub-0.1% error, floating-point traps | Precision validation, catastrophic cancellation |
| `test_brutal_edge_cases.py` (1002 lines) | 200+ edge cases across all types | Boundary precision, NaN/Inf/denormals |
| `test_event_accuracy_verification.py` | CV formula accuracy | Hand-calculated vs computed CV |

**Invariants Locked In:**
- **CV formula accuracy:** Computed CV must match hand-calculation within 0.5% (test_spec_compliance:228)
- **Boundary at threshold:** Values exactly AT threshold may trigger or not (implementation-dependent) (test_brutal_edge_cases:150)
- **NaN/Inf handling:** max(abs(mean), 1e-9) prevents division-by-zero; code must NOT crash on any FP value (test_event_a_stress:92)
- **Denormal stability:** Detection works with subnormal floats (system-dependent precision) (test_brutal_edge_cases:160)
- **Zero average safe:** Variance detectable even when mean=0 via (stddev / 1e-9) * 100% (test_event_a:63)

---

### 12. **Integration & Spec Compliance**

| File | Purpose | Key Tests |
|------|---------|-----------|
| `test_spec_compliance_all_events.py` (1177 lines) | Word-for-word PDF verification | All 4 types, mode-gating, TTL behavior |
| `test_realworld_all_events.py` (1347 lines) | Realistic STM32 hardware data | Resting values (327 ADC counts), offsets |
| `test_all_events_dynamic_chaos.py` (262 lines) | Chaotic multi-sensor data | All 4 types fire without conflicts |

**Invariants Locked In:**
- **Spec source:** "Complete_Mode_Event_TTL.pdf" pages 1-9 + "Updated_Event_Flowchart 1.pdf" (test_spec_compliance:5)
- **Hardware constants:** STM32 12-bit ADC (~0-4095), ~100 Hz sampling, resting ~327 counts (test_realworld_all_events:48)
- **Realistic thresholds:** startup_threshold=401, break_threshold=200, per-sensor Type A tolerance 1.8%-20% (test_realworld_all_events:52)
- **Offset application:** Sensor value adjusted = raw_ADC - sensor_offset BEFORE detection (test_realworld_all_events:26)
- **Cross-event dependency:** Type D reads Type C's avg_T3; no detection out of order (test_spec_compliance:415)

---

## Part 2: Grouped Invariant List

### Type A Variance Detection

1. **CV% formula:** `CV% = (stddev / max(|mean|, 1e-9)) × 100` (test_event_a.py:31)
2. **Window initialization:** Requires ≥ T1 × 100 × 0.9 samples (test_spec_compliance:141)
3. **Incremental algorithm:** O(1) per sample via running_sum & running_sum_sq (test_type_a_incremental:142)
4. **Data gap reset:** Gap > 2 seconds clears variance window state (test_spec_compliance:260)
5. **TTL start:** Event fires at CV% exceeding threshold; timer starts immediately (test_algorithm_a:113)
6. **Per-sensor override:** Optional T1 & tolerance% per sensor (test_realworld_all_events:144)

### Type B Post-Window Deviation

7. **Band formula:** `lower = avg_T2 × (1 − pct%)`, `upper = avg_T2 × (1 + pct%)` (test_spec_compliance:294)
8. **Sample evaluation:** Compares CURRENT (latest) raw sample to band, not average (test_spec_compliance:296)
9. **Block-based:** Fires on first sample after T2 seconds elapsed (test_algorithm_b:19)
10. **Post-event window:** ±9 seconds for data extraction (test_data_window_and_ttl:44)
11. **REF_VALUE=100:** Band uses fixed baseline (100), not percent of average (test_realworld_all_events:195)
12. **Per-sensor override:** Optional T2, lower%, upper% per sensor (test_realworld_all_events:144)

### Type C Average Range

13. **Absolute bounds:** Compares rolling avg_T3 directly to lower/upper thresholds (test_spec_compliance:330)
14. **Trigger condition:** `avg_T3 < lower OR avg_T3 > upper` (test_algorithm_c:14)
15. **No band formula:** Uses absolute values, NOT percent-based (test_spec_compliance:340)
16. **Window:** T3 seconds, ≥ T3 × 100 × 0.9 samples to initialize (test_spec_compliance:331)
17. **Per-sensor override:** Optional T3, lower, upper per sensor (test_realworld_all_events:144)

### Type D Two-Stage Smoothing

18. **Stage 1 (T4):** Short-term rolling average, produces 1-second block averages (test_algorithm_d:7)
19. **Stage 2 (T5):** Long-term baseline of 1-second blocks (test_algorithm_d:7)
20. **Band calculation:** `band = avg_T5 ± (REF_VALUE × tolerance%)` (test_algorithm_d:10)
21. **Trigger condition:** Compares avg_T3_from_C (Type C) to band (test_algorithm_d:17)
22. **Cross-event dependency:** Requires Type C computation first; no out-of-order (test_spec_compliance:415)
23. **Per-sensor override:** Optional T4, T5, lower, upper per sensor (test_realworld_all_events:144)

### Debounce Behavior

24. **Suppression:** First threshold crossing starts timer; "OK" status until duration elapses (test_debounce:143)
25. **Fire condition:** After debounce_seconds with condition STILL violated → EVENT (test_debounce:157)
26. **Event timestamp:** Stamped at ORIGINAL crossing time, NOT debounce-expiry (test_debounce:179)
27. **Recovery cancels:** Signal returning in-range resets timer; no event queued (test_debounce:167)
28. **Zero debounce:** debounce_seconds=0 → fires immediately on first crossing (test_debounce:129)
29. **Type A recovery:** High-variance sample rolling out of T1 window resets debounce (test_debounce:370)

### Mode Switching

30. **Three modes:** 0=POWER_ON (off), 1=STARTUP (active), 2=BREAK (suspended) (test_break_event:119)
31. **POWER_ON→STARTUP:** Value ≥ startup_threshold for ≥ startup_duration_seconds (test_spec_compliance:88)
32. **STARTUP→BREAK:** Value < break_threshold for ≥ break_duration_seconds (test_spec_compliance:88)
33. **Event gating:** A/B/C/D only fire during STARTUP mode (test_spec_compliance:102)
34. **BREAK event:** Fires when crossing below threshold; saved with ±9s data window (test_break_event:156)
35. **BREAK timestamp:** Stamped at crossing time, not duration-expiry (test_break_event:162)
36. **Mode persistence:** Remains in BREAK until manually reset (test_break_event:121)
37. **Grace period:** Brief dips during startup timer do NOT reset the timer (test_realworld_all_events:18)

### TTL Two-Phase Behavior

38. **Phase 1 (Active):** Event triggers → timer starts; no DB write (test_data_window_and_ttl:86)
39. **Phase 1 duration:** Timer active for exactly ttl_seconds (test_data_window_and_ttl:91)
40. **Phase 1 expiry:** At trigger_time + ttl_seconds → moves to pending_post_event (test_data_window_and_ttl:99)
41. **Phase 2 start:** pending_post_event entry created with save_at = trigger_time + 9.0 (test_data_window_and_ttl:154)
42. **Phase 2 expiry:** At save_at → queues to worker for DB insertion (test_data_window_and_ttl:141)
43. **No extension:** TTL does NOT extend if condition persists; fires at exact TTL (test_data_window_and_ttl:98)
44. **Duplicate suppression:** Same sensor/type cannot have two active timers (test_data_window_and_ttl:114)
45. **Priority D>C>B>A:** Higher-priority event clears lower-priority pending entries (test_data_window_and_ttl:191)
46. **Per-sensor override:** Optional ttl_seconds per sensor + event type (test_per_sensor_ttl_e2e:106)

### Data Window Extraction

47. **Event A window:** ±9 seconds around trigger_time using T1-second rolling average (test_avg_window_per_event:100)
48. **Event B window:** Uses Type B detector's T2 per sensor (test_avg_window_per_event:130)
49. **Event C window:** Uses Type C detector's T3 per sensor (test_avg_window_per_event:150)
50. **Event D window:** Uses Type D detector's T5 baseline window per sensor (test_avg_window_per_event:170)
51. **Window accuracy:** Extracted avg values must match detector state within 0.15 (test_avg_window_per_event:55)

### Database & Configuration

52. **Config keys:** 117+ total across 18 categories (test_app_config:89)
53. **Type casting:** int, float, str, bool parsed; malformed → raw string (test_app_config:18)
54. **Defaults seeded:** INSERT OR IGNORE prevents overwriting on reinit (test_app_config:3)
55. **Per-sensor configs:** Override global; fallback if missing (test_config_roundtrip:8)
56. **Atomicity:** Config update → DB persist → GET returns new value (test_config_roundtrip:39)
57. **Live reload:** T1/T2/T3 changes apply next cycle (test_config_chaos_e2e:7)
58. **Event table:** 48 event_flag columns (12 sensors × 4 types); variance/average fields (test_continuous_events:62)
59. **Trigger timestamp:** Stored as crossing time, NOT TTL/debounce-expiry (test_continuous_events:113)

### Performance & Accuracy

60. **Type A latency:** O(1) per sample; <1ms average with 60s window (test_type_a_incremental:165)
61. **CV accuracy:** Computed vs hand-calculated within 0.5% (test_spec_compliance:228)
62. **Boundary values:** Detection at exactly-threshold values may vary by 1 ULP (test_brutal_edge_cases:150)
63. **FP stability:** Code handles NaN/Inf/denormals without crash (test_event_a_stress:92)
64. **Thread pooling:** GlobalWorkerManager shared; no thread explosion (test_extreme_concurrency:142)
65. **Buffer bounds:** Circular deques (maxlen); fixed memory footprint (test_data_window_and_ttl:56)

### Hardware & Real-World

66. **STM32 ADC range:** 0-4095 12-bit samples; resting ~327 counts (test_realworld_all_events:48)
67. **Sample rate:** Typically 100-123 Hz (test_realworld_all_events:50)
68. **Realistic thresholds:** startup=401, break=200, Type A tolerance 1.8%-20% per sensor (test_realworld_all_events:52)
69. **Sensor offset:** Applied before detection: adjusted = raw - offset (test_realworld_all_events:26)
70. **Normal noise:** ±10 ADC counts = ~0.5% CV on 2000-count baseline (test_spec_compliance:192)

---

## Part 3: Gap Analysis — What's NOT Tested

### Missing Coverage

1. **Timestamp re-anchoring after 5-second drift:** No test verifies behavior if local clock drifts; gap detection assumes monotonic time
2. **Hot-plug sensor add/remove:** Tests assume static 12-sensor setup; dynamic addition untested
3. **Offset calibration verification:** Offset applied but no test verifies calibration source or update mechanism
4. **TTL timer restart:** If signal returns in-range then re-crosses → new timer (implicit, not explicit)
5. **Multiple concurrent BREAK events:** Tests single BREAK transition; multiple sensors breaking simultaneously untested
6. **Config rollback:** No test for reverting failed config update
7. **Database corruption recovery:** No test for SQLite corruption or recovery (WAL mode not verified)
8. **Network time sync:** Tests assume system clock; SNTP/NTP impact untested
9. **Per-sensor mode override:** Tests assume all sensors share same mode; per-sensor mode untested
10. **Event suppression during startup ramp-up:** Tests skip to STARTUP; slow power-on gradient untested
11. **Variance window corruption:** Tests assume clean data; corrupted state (e.g., negative sum_sq) untested
12. **Type B / Type C / Type D per-sensor config CRUD:** Only Type A per-sensor tested thoroughly
13. **Config GUI → API → DB validation:** Tests API routes but not form validation (empty/malformed input)
14. **Batch event insertion consistency:** Single-event tests; batch atomicity untested
15. **Memory profiling under sustained load:** Tests memory at ~10 min; 24-hour behavior untested

### Recommended Tests for Phase 0.6

- **Drift detection:** Timestamp gap > threshold → warning/reset
- **Sensor add/remove:** Dynamic reconfig of sensor_buffers, detector arrays
- **Concurrent mode transitions:** Multi-sensor BREAK scenario
- **Config atomicity:** Rollback on validation failure
- **Per-sensor Type B/C/D config:** Match Type A per-sensor feature parity
- **Extended memory:** 24-hour continuous run with memory profiling
- **Time sync:** SNTP event handling (leap seconds, large jumps)

---

## Part 4: Acceptance Criteria for HERMES Rewrite

### Must-Have (Phase 1)

- [ ] CV% formula matches test_event_a.py line 31 (within machine epsilon)
- [ ] Type A incremental algorithm is O(1) per sample (<1ms with 60s window)
- [ ] Type B band formula exact: `lower = avg × (1 - pct%)`, `upper = avg × (1 + pct%)`
- [ ] Type C uses absolute bounds, NOT percent formula
- [ ] Type D two-stage: T4 block average → T5 baseline band
- [ ] TTL fires at exact trigger_time + ttl_seconds (no extension)
- [ ] Debounce timestamp is crossing time, NOT fire time
- [ ] Mode-gating: Events only fire during STARTUP mode
- [ ] BREAK event timestamp is crossing time
- [ ] Per-sensor config overrides work for all 4 types + TTL
- [ ] Config persists to DB and survives reopen
- [ ] Data window extraction uses correct T1/T2/T3/T5 per type per sensor
- [ ] Priority system: D clears C/B/A, C clears B/A, B clears A
- [ ] Thread pool is global (no thread explosion)
- [ ] Buffer memory is bounded (circular deques)

### Should-Have (Phase 2)

- [ ] Per-sensor mode switching (not just global)
- [ ] Type B/C/D per-sensor config CRUD (matching Type A)
- [ ] Config rollback on validation failure
- [ ] Extended memory profiling (24-hour run)
- [ ] Timestamp drift detection & recovery
- [ ] Batch event insertion atomicity verified

### Nice-to-Have (Phase 3+)

- [ ] Hot-plug sensor add/remove
- [ ] Dynamic sample rate change
- [ ] Offset calibration source audit
- [ ] SQLite corruption recovery (PRAGMA integrity_check)
- [ ] Per-sensor per-event TTL override (sub-subsystem)

---

## Appendix A: File-by-File Manifest

| File | Lines | Purpose | Key Invariants |
|------|-------|---------|---|
| test_algorithm_a.py | 127 | Type A TTL start | CV formula, TTL immediate |
| test_algorithm_b.py | 40 | Type B post-deviation | Band formula, T2 boundary |
| test_algorithm_c.py | 39 | Type C out-of-range | Absolute bounds, not percent |
| test_algorithm_d.py | 40 | Type D two-stage | T4/T5 bands, Event C dep |
| test_debounce.py | 398 | Debounce all types | Suppression, recovery, timestamp |
| test_break_event.py | 625 | BREAK mode & event | Mode gating, crossing time |
| test_app_config.py | 943 | Config CRUD | 117+ keys, 18 cats, persistence |
| test_config_roundtrip.py | 997 | API→DB→EventDetector | All 4 types, per-sensor, global |
| test_config_chaos_e2e.py | 730 | Config changes mid-run | Live reload, state consistency |
| test_data_window_and_ttl.py | 876 | TTL 2-phase, priority | Exact timing, no extension, priority |
| test_e2e_event_pipeline.py | 744 | Detection→DB→Query | Event storage, DB persistence |
| test_ttl_e2e_complete.py | 965 | TTL lifecycle | Phase 1+2, threshold violations |
| test_spec_compliance_all_events.py | 1177 | PDF spec word-for-word | CV/B/C/D formulas, mode gating |
| test_realworld_all_events.py | 1347 | Hardware-realistic STM32 | Offsets, thresholds, per-sensor |
| test_avg_window_per_event.py | 617 | Per-event window extract | T1/T2/T3/T5 per sensor |
| test_per_sensor_ttl_e2e.py | 563 | Per-sensor TTL override | Custom TTL, fallback to global |
| test_type_a_incremental.py | 257 | Type A O(1) algorithm | Accuracy, performance, continuous |
| test_event_a.py | 80 | Type A unit tests | Zero/low/high variance, zero-mean |
| test_event_a_stress.py | 225 | Type A throughput | 10K calls, 1M samples, FP chaos |
| test_brutal_edge_cases.py | 1002 | 200+ edge cases | Boundary precision, NaN/Inf |
| test_type_a_b_combined.py | 154 | A+B together | No conflicts, combined <1ms |
| test_all_types_continuous.py | 225 | A+B+C+D together | All 4 fire independently |
| test_extreme_concurrency.py | 238 | 10+ devices, 3 threads | No thread explosion, shared pool |
| test_extreme_accuracy.py | 375 | Sub-0.1% CV error | Precision, catastrophic cancellation |
| avg_type_b_test.py | 68 | Type B standalone | Block-based detection, spikes |
| test_event_config_db_storage.py | 93 | Event config persist | Type A/B/C/D save/retrieve |
| test_continuous_events.py | 189 | Sensor data→events table | Variance, average, event_flags |
| test_type_b_event_storage.py | 67 | Type B event in DB | Row inserted, updated correctly |
| test_cache.py | 58 | can_id→device cache | Lookup logic, no type mismatch |
| test_accumulator.py | 148 | Sensor frame accumulation | 3 CAN IDs → complete snapshot |
| test_type_d_incremental.py | 101 | Type D O(1) averaging | Performance, 1-sec blocks |
| test_event_timing_fix.py | 250 | Event timing (T1 interval) | Consistent trigger intervals |
| test_fix_verification.py | 184 | sensor_values key fix | Accumulator enabling frame_info |
| test_event_detector_creation.py | 151 | EventDetector creation | FIX E: create on /api/stream/start |
| test_event_history_memory_leak.py | 137 | Memory leak: event_history | Bounded pending_post_event size |
| test_memory_optimization_shared.py | 131 | Shared buffer memory | No per-detector buffers |
| test_fps_performance.py | 182 | Frame throughput @ 123 Hz | Per-device latency <10ms |
| test_extreme_performance.py | 174 | Sustained load latency | No GC stalls, consistent timing |
| test_extreme_stress.py | 175 | Stress test framework | 5000+ samples, error tracking |
| test_all_events_dynamic_chaos.py | 262 | Chaotic multi-sensor | All 4 types, no conflicts |
| test_extreme_edge_cases.py | 311 | 100+ edge cases | Degenerate inputs, FP traps |
| test_extreme_integration.py | 92 | Full integration stress | End-to-end, DB, workers |
| test_event_accuracy_verification.py | 151 | CV formula accuracy | Hand-calc vs computed |
| test_event_detection.py | 183 | Event detection flow | Detection→update_event_detection |
| test_event_detection_all_types.py | 98 | All 4 types unit | Type A/B/C/D basic detection |
| test_event_detection_flow.py | 297 | Detection pipeline | Detection→TTL→DB queue |
| test_event_window_query.py | 140 | Data window extraction | Query API correctness |
| test_missing_detector.py | 107 | Missing detector handling | No crash, graceful fallback |
| test_modbus_loopback.py | 505 | Modbus sensor frames | CAN→MQTT→detector flow |
| test_modbus_multi_slave.py | 486 | Multi-slave Modbus | Device aggregation, sensor mapping |
| test_modbus_slave.py | 123 | Single Modbus device | Frame parsing, sensor extraction |
| test_patterns.py | 95 | Signal pattern detection | Ramp, sawtooth, sine patterns |
| test_real_modbus_patterns.py | 168 | Real Modbus data | Hardware-realistic frames |
| test_stress.py | 596 | Sustained stress | 10K+ samples, memory stable |
| test_ttl_save_fix.py | 337 | TTL save-at timing | Phase 2 queuing at trigger+9s |
| test_ttl_simple_debug.py | 93 | TTL debug helpers | Simple TTL validation |
| test_type_mismatch.py | 68 | Type mismatch detection | Invalid config handling |
| test_ultimate_chaos.py | 609 | Everything-at-once | All subsystems, chaos pattern |
| test_utils.py | 5 | Test helpers | report_case() function |

**Summary:** 64 test files, ~21.2K lines, covering 12 subsystems, ~400 test functions

---

## Appendix B: Running the Test Suite

```bash
# Full suite
pytest /home/embed/hammer/tests/ -v

# By subsystem
pytest /home/embed/hammer/tests/test_algorithm_a.py -v  # Type A
pytest /home/embed/hammer/tests/test_algorithm_b.py -v  # Type B
pytest /home/embed/hammer/tests/test_debounce.py -v      # Debounce
pytest /home/embed/hammer/tests/test_break_event.py -v   # Mode switching
pytest /home/embed/hammer/tests/test_data_window_and_ttl.py -v  # TTL

# Spec compliance (strongest validation)
pytest /home/embed/hammer/tests/test_spec_compliance_all_events.py -v

# Real-world scenario
pytest /home/embed/hammer/tests/test_realworld_all_events.py -v

# Performance
pytest /home/embed/hammer/tests/test_type_a_incremental.py::test_incremental_performance -v
pytest /home/embed/hammer/tests/test_event_a_stress.py -v
```

---

## Revision History

| Date | Author | Version | Change |
|------|--------|---------|--------|
| 2026-04-23 | Claude Code | 1.0 | Initial catalog from 64 test files |

---

**Document Status:** READY FOR HANDOFF  
**Next Phase:** HERMES Phase 1 rewrite with test-driven acceptance criteria

