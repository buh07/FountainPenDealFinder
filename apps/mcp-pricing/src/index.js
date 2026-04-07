import readline from "node:readline";

const API_BASE = process.env.MCP_API_BASE || "http://localhost:8000";

const TOOL_DEFS = [
	{
		name: "predict_resale",
		description: "Run resale prediction for listing_id",
		inputSchema: { listing_id: "required listing id" },
	},
	{
		name: "predict_auction",
		description: "Run auction prediction for listing_id",
		inputSchema: { listing_id: "required listing id" },
	},
	{
		name: "score_listing",
		description: "Rescore one listing and return listing summary",
		inputSchema: { listing_id: "required listing id" },
	},
	{
		name: "run_retrain_job",
		description: "Run baseline retrain pipeline",
		inputSchema: {},
	},
];

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

	if (tool === "predict_resale") {
		if (!args?.listing_id) throw new Error("listing_id is required");
		return apiPost(`/predict/resale/${encodeURIComponent(String(args.listing_id))}`);
	}

	if (tool === "predict_auction") {
		if (!args?.listing_id) throw new Error("listing_id is required");
		return apiPost(`/predict/auction/${encodeURIComponent(String(args.listing_id))}`);
	}

	if (tool === "score_listing") {
		if (!args?.listing_id) throw new Error("listing_id is required");
		return apiPost(`/score/${encodeURIComponent(String(args.listing_id))}`);
	}

	if (tool === "run_retrain_job") {
		return apiPost(`/retrain/jobs`);
	}

	throw new Error(`unknown tool: ${tool}`);
}

const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: false });

console.log(JSON.stringify({ event: "ready", service: "mcp-pricing", tools: TOOL_DEFS.map((t) => t.name) }));

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
