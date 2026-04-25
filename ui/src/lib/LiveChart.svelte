<script lang="ts">
	/*
	 * LiveChart — uPlot wrapper that streams samples from /api/live_stream/{id}
	 * over Server-Sent Events and keeps a sliding window in view.
	 *
	 * Why uPlot over Chart.js / ECharts:
	 *   * It paints 12 series × thousands of samples in <1 ms per frame on a
	 *     mid-range laptop, where ECharts starts dropping frames at 1500
	 *     samples (the legacy dashboard hit this exact wall).
	 *   * Tiny bundle — ~40 kB, no canvas-painter framework on top.
	 *   * Imperative API fits Svelte's lifecycle cleanly.
	 *
	 * Data shape uPlot expects: column-major arrays —
	 *   data[0] = timestamps (epoch seconds, monotonically increasing)
	 *   data[1..12] = per-sensor value arrays (same length as data[0])
	 *
	 * We keep a single in-place backing buffer and call setData() per SSE
	 * frame. No row→column transpose on the hot path.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import uPlot from 'uplot';
	import type { Options as UPlotOptions } from 'uplot';

	interface SensorSnapshot {
		ts: number;
		values: Record<string, number>;
	}
	interface SsePayload {
		device_id: number;
		samples: SensorSnapshot[];
	}

	interface Props {
		deviceId: number;
		/** Sliding-window length in seconds. */
		windowSeconds: number;
		/** Optional set of sensor_ids (1..12) to display; defaults to all. */
		visibleSensors?: Set<number>;
		/** Server-side poll interval; lower = smoother but more CPU. */
		intervalSeconds?: number;
	}

	const {
		deviceId,
		windowSeconds,
		visibleSensors,
		intervalSeconds = 0.1
	}: Props = $props();

	// Tailwind-aligned palette: 12 distinguishable colours that work on
	// both light and dark backgrounds without further tweaking.
	const SENSOR_COLOURS: readonly string[] = [
		'#ef4444', // 1  red-500
		'#f97316', // 2  orange-500
		'#eab308', // 3  yellow-500
		'#22c55e', // 4  green-500
		'#10b981', // 5  emerald-500
		'#06b6d4', // 6  cyan-500
		'#3b82f6', // 7  blue-500
		'#6366f1', // 8  indigo-500
		'#8b5cf6', // 9  violet-500
		'#a855f7', // 10 purple-500
		'#ec4899', // 11 pink-500
		'#64748b'  // 12 slate-500
	];

	let containerEl: HTMLDivElement | undefined = $state();
	let chart: uPlot | undefined;
	let eventSource: EventSource | undefined;
	let connectionStatus = $state<'connecting' | 'open' | 'error'>('connecting');
	let sampleCount = $state(0);

	// Backing buffer. Index 0 holds timestamps; indices 1..12 hold per-sensor
	// values (1-indexed sensor IDs map to data[sensorId]).
	const NUM_SENSORS = 12;
	let timestamps: number[] = [];
	const sensorValues: number[][] = Array.from({ length: NUM_SENSORS }, () => []);

	function sensorBuffer(sid: number): number[] {
		// `noUncheckedIndexedAccess` widens to `T | undefined`; the buffers
		// are pre-allocated in the module-level `sensorValues = Array.from(...)`,
		// so a non-null assertion is correct here and keeps the hot loop
		// allocation-free.
		return sensorValues[sid - 1]!;
	}

	function clearBuffers() {
		timestamps = [];
		for (let i = 0; i < NUM_SENSORS; i++) sensorValues[i] = [];
	}

	function trimWindow(now: number) {
		const horizon = now - windowSeconds;
		let cut = 0;
		while (cut < timestamps.length && timestamps[cut]! < horizon) cut++;
		if (cut > 0) {
			timestamps.splice(0, cut);
			for (let i = 0; i < NUM_SENSORS; i++) sensorBuffer(i + 1).splice(0, cut);
		}
	}

	function appendSamples(samples: SensorSnapshot[]) {
		if (samples.length === 0) return;
		for (const sample of samples) {
			timestamps.push(sample.ts);
			for (let sid = 1; sid <= NUM_SENSORS; sid++) {
				const v = sample.values[String(sid)];
				const buf = sensorBuffer(sid);
				if (typeof v === 'number') {
					buf.push(v);
				} else {
					// Hold-last-value if a sensor went missing this tick. Matches
					// the legacy LiveDataHub.add_snapshot behaviour and prevents
					// the line from snapping to zero on transient gaps.
					buf.push(buf.length > 0 ? buf[buf.length - 1]! : 0);
				}
			}
		}
		const latest = timestamps[timestamps.length - 1];
		if (latest !== undefined) trimWindow(latest);
		sampleCount = timestamps.length;
	}

	function buildOptions(width: number, height: number): UPlotOptions {
		const series: UPlotOptions['series'] = [
			{} // x-axis (timestamps)
		];
		// Match legacy ECharts step:'end' — flat segments between successive
		// samples, prevents straight-line interpolation across missed samples.
		// Built once and reused across all 12 series.
		const steppedPath = uPlot.paths?.stepped?.({ align: 1 });

		for (let sid = 1; sid <= NUM_SENSORS; sid++) {
			const colour = SENSOR_COLOURS[sid - 1] ?? '#64748b';
			const seriesEntry: NonNullable<UPlotOptions['series']>[number] = {
				label: `S${sid}`,
				stroke: colour,
				width: 1.25,
				show: visibleSensors === undefined || visibleSensors.has(sid)
			};
			if (steppedPath) seriesEntry.paths = steppedPath;
			series.push(seriesEntry);
		}
		return {
			width,
			height,
			scales: {
				x: { time: true }
			},
			axes: [
				{ stroke: 'rgba(120,120,120,0.8)' },
				{ stroke: 'rgba(120,120,120,0.8)' }
			],
			legend: { show: true },
			cursor: { drag: { x: false, y: false } },
			series
		};
	}

	function getCurrentData(): uPlot.AlignedData {
		return [timestamps, ...sensorValues] as uPlot.AlignedData;
	}

	function applyVisibility() {
		if (!chart) return;
		for (let sid = 1; sid <= NUM_SENSORS; sid++) {
			const shouldShow = visibleSensors === undefined || visibleSensors.has(sid);
			const series = chart.series[sid];
			if (series && series.show !== shouldShow) {
				chart.setSeries(sid, { show: shouldShow });
			}
		}
	}

	function openStream() {
		const url = `/api/live_stream/${deviceId}?interval=${intervalSeconds}`;
		eventSource = new EventSource(url);
		eventSource.onopen = () => (connectionStatus = 'open');
		eventSource.onerror = () => (connectionStatus = 'error');
		eventSource.onmessage = (ev) => {
			try {
				const payload = JSON.parse(ev.data) as SsePayload;
				appendSamples(payload.samples);
				chart?.setData(getCurrentData());
			} catch {
				// Malformed frame — keep the stream open; one bad frame
				// shouldn't kill the chart.
			}
		};
	}

	function closeStream() {
		eventSource?.close();
		eventSource = undefined;
	}

	onMount(() => {
		if (!containerEl) return;
		const rect = containerEl.getBoundingClientRect();
		chart = new uPlot(
			buildOptions(rect.width, Math.max(280, rect.height)),
			getCurrentData(),
			containerEl
		);

		// Resize when the container changes.
		const observer = new ResizeObserver(() => {
			if (!chart || !containerEl) return;
			const r = containerEl.getBoundingClientRect();
			chart.setSize({ width: r.width, height: Math.max(280, r.height) });
		});
		observer.observe(containerEl);

		openStream();

		return () => {
			observer.disconnect();
		};
	});

	onDestroy(() => {
		closeStream();
		chart?.destroy();
		chart = undefined;
	});

	// React to deviceId changes by reconnecting the stream and clearing data.
	$effect(() => {
		// Read deps so $effect tracks them.
		const _ = deviceId;
		void _;
		untrack(() => {
			closeStream();
			clearBuffers();
			chart?.setData(getCurrentData());
			openStream();
		});
	});

	// React to visibility changes.
	$effect(() => {
		void visibleSensors;
		untrack(applyVisibility);
	});
</script>

<div class="flex flex-col gap-2">
	<div class="flex items-center justify-between text-xs text-neutral-500">
		<span>
			{#if connectionStatus === 'open'}
				<span class="text-green-600 dark:text-green-400">● live</span>
			{:else if connectionStatus === 'connecting'}
				connecting…
			{:else}
				<span class="text-red-600 dark:text-red-400">● disconnected</span>
			{/if}
		</span>
		<span class="font-mono">
			{sampleCount} samples · {windowSeconds}s window
		</span>
	</div>
	<div bind:this={containerEl} class="h-80 w-full"></div>
</div>
