import readline from "node:readline";

const API_BASE = process.env.MCP_API_BASE || "http://localhost:8000";

const TOOL_DEFS = [
	{
		name: "search_listings",
		description: "Search ranked listings from internal API",
		inputSchema: {
			source: "optional marketplace source",
			bucket: "optional deal bucket",
			limit: "optional integer limit",
		},
	},
	{
		name: "get_listing_detail",
		description: "Fetch one listing summary by listing_id",
		inputSchema: { listing_id: "required listing id" },
	},
	{
		name: "refresh_ending_auctions",
		description: "Trigger ending-auctions refresh job",
		inputSchema: { window_hours: "optional 1-72" },
	},
];

async function apiGet(path) {
	const response = await fetch(`${API_BASE}${path}`);
	if (!response.ok) {
		const text = await response.text();
		throw new Error(`GET ${path} failed: ${response.status} ${text}`);
	}
	return response.json();
}

async function apiPost(path, body) {
	const response = await fetch(`${API_BASE}${path}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: body ? JSON.stringify(body) : undefined,
	});
	if (!response.ok) {
		const text = await response.text();
		throw new Error(`POST ${path} failed: ${response.status} ${text}`);
	}
	return response.json();
}

async function runTool(tool, args) {
	if (tool === "tools") {
		return TOOL_DEFS;
	}

	if (tool === "search_listings") {
		const params = new URLSearchParams();
		if (args?.source) params.set("source", String(args.source));
		if (args?.bucket) params.set("bucket", String(args.bucket));
		params.set("limit", String(args?.limit || 25));
		return apiGet(`/listings?${params.toString()}`);
	}

	if (tool === "get_listing_detail") {
		if (!args?.listing_id) throw new Error("listing_id is required");
		return apiGet(`/listings/${encodeURIComponent(String(args.listing_id))}`);
	}

	if (tool === "refresh_ending_auctions") {
		const hours = Number(args?.window_hours || 24);
		return apiPost(`/collect/refresh-ending?window_hours=${encodeURIComponent(String(hours))}`);
	}

	throw new Error(`unknown tool: ${tool}`);
}

const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: false });

console.log(JSON.stringify({ event: "ready", service: "mcp-browser", tools: TOOL_DEFS.map((t) => t.name) }));

rl.on("line", async (line) => {
	const raw = line.trim();
	if (!raw) return;

	let message;
	try {
		message = JSON.parse(raw);
	} catch {
		console.log(JSON.stringify({ ok: false, error: "invalid_json" }));
		return;
	}

	try {
		const result = await runTool(message.tool, message.args || {});
		console.log(JSON.stringify({ id: message.id || null, ok: true, result }));
	} catch (error) {
		console.log(
			JSON.stringify({
				id: message.id || null,
				ok: false,
				error: String(error instanceof Error ? error.message : error),
			})
		);
	}
});
