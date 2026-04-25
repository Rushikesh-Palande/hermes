/*
 * TypeScript mirrors of the FastAPI response shapes.
 *
 * Hand-maintained to match `services/hermes/api/routes/*` Pydantic
 * models. Drift is caught by the CI typecheck job — `pnpm check`
 * fails if a UI consumer of these types references a field that no
 * longer exists. Keep field names byte-identical with the Pydantic
 * models so JSON.parse output binds without remapping.
 *
 * Phase 5+ may swap this file for openapi-generated types pulled
 * from the live `/openapi.json`. For now hand-rolled is faster.
 */

export type EventType = 'A' | 'B' | 'C' | 'D' | 'BREAK';
export type DeviceProtocol = 'mqtt' | 'modbus_tcp';

export interface DeviceOut {
	device_id: number;
	name: string;
	protocol: DeviceProtocol;
	topic: string | null;
	is_active: boolean;
	created_at: string;
	updated_at: string;
}

export interface DeviceIn {
	device_id: number;
	name: string;
	protocol?: DeviceProtocol;
	topic?: string | null;
}

export interface EventOut {
	event_id: number;
	triggered_at: string;
	fired_at: string;
	session_id: string;
	device_id: number;
	sensor_id: number;
	event_type: EventType;
	triggered_value: number;
	metadata: Record<string, unknown>;
	window_id: number | null;
}

export interface EventWindowOut {
	window_id: number;
	event_id: number;
	start_ts: string;
	end_ts: string;
	sample_rate_hz: number;
	sample_count: number;
	encoding: string;
	samples: Array<[number, number]>;
}

export interface TypeAConfig {
	enabled: boolean;
	T1: number;
	threshold_cv: number;
	debounce_seconds: number;
	init_fill_ratio: number;
	expected_sample_rate_hz: number;
}

export interface TypeBConfig {
	enabled: boolean;
	T2: number;
	lower_threshold_pct: number;
	upper_threshold_pct: number;
	debounce_seconds: number;
	init_fill_ratio: number;
	expected_sample_rate_hz: number;
}

export interface TypeCConfig {
	enabled: boolean;
	T3: number;
	threshold_lower: number;
	threshold_upper: number;
	debounce_seconds: number;
	init_fill_ratio: number;
	expected_sample_rate_hz: number;
}

export interface TypeDConfig {
	enabled: boolean;
	T4: number;
	T5: number;
	tolerance_pct: number;
	debounce_seconds: number;
	init_fill_ratio: number;
	expected_sample_rate_hz: number;
}

export interface HealthResponse {
	status: string;
	version: string;
}

export type DetectorTypeName = 'type_a' | 'type_b' | 'type_c' | 'type_d';

export interface SensorOverrideOut {
	device_id: number;
	sensor_id: number;
	config: Record<string, unknown>;
}

export interface OverridesOut {
	/** device_id (as a string key) → config object */
	devices: Record<string, Record<string, unknown>>;
	sensors: SensorOverrideOut[];
}
