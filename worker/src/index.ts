/**
 * Static-asset dashboard (public/index.html + public/data/results.json).
 * Cloudflare serves matching asset paths directly; this handler only runs
 * as the fallback for anything that doesn't match a static file -- which
 * includes our one API route below.
 */

declare global {
	interface Env {
		// Set via `wrangler secret put TWELVEDATA_API_KEY` -- never committed.
		TWELVEDATA_API_KEY?: string;
	}
}

const TWELVEDATA_BASE = 'https://api.twelvedata.com';

function json(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
	});
}

async function fetchTwelveData(path: string, params: Record<string, string>): Promise<any> {
	const url = new URL(TWELVEDATA_BASE + path);
	for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
	const res = await fetch(url.toString());
	const data: any = await res.json();
	if (!res.ok || data.status === 'error') {
		throw new Error(data.message || `Twelve Data request failed (${res.status})`);
	}
	return data;
}

/**
 * The real SPX index symbol is gated behind Twelve Data's paid Grow/Venture
 * plan (confirmed: /quote?symbol=SPX returns a 404 upgrade-required error on
 * the free tier). SPY -- a liquid ETF tracking the same index, with real
 * volume -- is available on the free tier for both quotes and VWAP, so every
 * figure here (price, open, prior close, VWAP) is derived from SPY and
 * scaled by a fixed SPX/SPY multiplier. This is the same style of
 * "SPY-derived SPX-proxy" approximation already used by the synthetic demo
 * backtest on this site, just applied live instead of historically. The
 * multiplier drifts slowly over time (dividends, rebalances) -- override it
 * with ?multiplier= if it's gotten noticeably stale, or upgrade the Twelve
 * Data plan and swap this back to a direct SPX quote.
 */
const DEFAULT_SPX_SPY_MULTIPLIER = 10;

async function handleMarketData(url: URL, env: Env): Promise<Response> {
	const apiKey = env.TWELVEDATA_API_KEY;
	if (!apiKey) {
		return json({ error: 'Market data API key is not configured on the server (TWELVEDATA_API_KEY secret missing).' }, 500);
	}

	const proxySymbol = url.searchParams.get('proxy') || 'SPY';
	const multiplierParam = parseFloat(url.searchParams.get('multiplier') || '');
	const multiplier = Number.isFinite(multiplierParam) && multiplierParam > 0 ? multiplierParam : DEFAULT_SPX_SPY_MULTIPLIER;

	try {
		const [proxyQuote, proxyVwap] = await Promise.all([
			fetchTwelveData('/quote', { symbol: proxySymbol, apikey: apiKey }),
			fetchTwelveData('/vwap', { symbol: proxySymbol, interval: '1min', outputsize: '1', apikey: apiKey }),
		]);

		const proxyClose = parseFloat(proxyQuote.close);
		const proxyOpen = parseFloat(proxyQuote.open);
		const proxyPrevClose = parseFloat(proxyQuote.previous_close);
		const proxyVwapLatest = parseFloat(proxyVwap?.values?.[0]?.vwap);

		const fields = { proxyClose, proxyOpen, proxyPrevClose, proxyVwapLatest };
		const bad = Object.entries(fields).filter(([, v]) => Number.isNaN(v)).map(([k]) => k);
		if (bad.length) {
			throw new Error(`Upstream response missing/invalid field(s): ${bad.join(', ')}. Check that "${proxySymbol}" is a valid Twelve Data symbol.`);
		}

		return json({
			proxySymbol,
			multiplier,
			priorClose: proxyPrevClose * multiplier,
			todayOpen: proxyOpen * multiplier,
			currentPrice: proxyClose * multiplier,
			vwap: proxyVwapLatest * multiplier,
			asOf: proxyQuote.datetime || new Date().toISOString(),
			note: `SPX real-time index data requires a paid Twelve Data plan, so every figure here is ${proxySymbol} (has real volume, free tier) × ${multiplier} — an approximation, not a live SPX quote. The ratio drifts slowly over time; override with ?multiplier= if it looks stale.`,
		});
	} catch (err) {
		return json({ error: err instanceof Error ? err.message : String(err) }, 502);
	}
}

export default {
	async fetch(request, env, ctx): Promise<Response> {
		const url = new URL(request.url);
		if (url.pathname === '/api/market-data') {
			return handleMarketData(url, env);
		}
		return new Response('Not Found', { status: 404 });
	},
} satisfies ExportedHandler<Env>;
