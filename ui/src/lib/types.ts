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

export interface SensorOffsetOut {
	sensor_id: number;
	offset_value: number;
	updated_at: string | null;
}

export interface DeviceOffsetsOut {
	device_id: number;
	offsets: SensorOffsetOut[];
}

export interface MqttBrokerOut {
	broker_id: number;
	host: string;
	port: number;
	username: string | null;
	has_password: boolean;
	use_tls: boolean;
	is_active: boolean;
	created_at: string;
}

export interface MqttBrokerIn {
	host: string;
	port?: number;
	username?: string | null;
	password?: string | null;
	use_tls?: boolean;
	is_active?: boolean;
}

export interface MqttBrokerPatch {
	host?: string;
	port?: number;
	username?: string | null;
	/**
	 * Password write semantics:
	 *   - omitted (undefined) → unchanged
	 *   - "" empty string    → cleared
	 *   - non-empty           → re-encrypted and stored
	 */
	password?: string;
	use_tls?: boolean;
	is_active?: boolean;
}

// ─── Packages ─────────────────────────────────────────────────────

export interface PackageOut {
	package_id: string;
	name: string;
	description: string | null;
	is_default: boolean;
	is_locked: boolean;
	created_at: string;
	created_by: string | null;
	archived_at: string | null;
	parent_package_id: string | null;
}

export interface PackageIn {
	name: string;
	description?: string | null;
}

// ─── Sessions ─────────────────────────────────────────────────────

export type SessionScope = 'global' | 'local';
export type SessionLogEvent =
	| 'start'
	| 'stop'
	| 'pause'
	| 'resume'
	| 'reconfigure'
	| 'error';

export interface SessionOut {
	session_id: string;
	scope: SessionScope;
	parent_session_id: string | null;
	device_id: number | null;
	package_id: string;
	started_at: string;
	ended_at: string | null;
	started_by: string | null;
	ended_reason: string | null;
	notes: string | null;
	record_raw_samples: boolean;
}

export interface SessionStart {
	scope: SessionScope;
	package_id: string;
	device_id?: number | null;
	notes?: string | null;
	record_raw_samples?: boolean;
}

export interface SessionStop {
	ended_reason?: string | null;
}

export interface SessionLogOut {
	log_id: number;
	session_id: string;
	event: SessionLogEvent;
	ts: string;
	actor: string | null;
	details: Record<string, unknown> | null;
}

export interface CurrentSessionsOut {
	global_session: SessionOut | null;
	local_sessions: SessionOut[];
}

// ─── System tunables (gap 8) ──────────────────────────────────────

export type IngestMode = 'all' | 'shard' | 'live_only';
export type TunableEditable = 'live' | 'restart' | 'via_other_route';

export interface TunableField {
	key: string;
	value: unknown;
	description: string;
	editable: TunableEditable;
	edit_hint: string | null;
}

export interface SystemStateOut {
	version: string;
	ingest_mode: IngestMode;
	shard_count: number;
	shard_index: number;
	dev_mode: boolean;
	log_format: string;
	active_global_session_id: string | null;
	active_local_session_count: number;
	sessions_recording_count: number;
	modbus_devices_active: number;
	mqtt_devices_active: number;
}

export interface SystemTunablesOut {
	state: SystemStateOut;
	tunables: TunableField[];
}
