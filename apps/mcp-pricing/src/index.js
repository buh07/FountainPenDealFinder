import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const API_BASE = process.env.MCP_API_BASE || "http://localhost:8000";

function asErrorEnvelope(code, message, details = null) {
	return JSON.stringify({ ok: false, code, message, details });
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

function okContent(payload) {
	return {
		content: [{ type: "text", text: JSON.stringify(payload) }],
	};
}

function errorContent(error, code) {
	const message = error instanceof Error ? error.message : String(error);
	return {
		isError: true,
		content: [{ type: "text", text: asErrorEnvelope(code, message) }],
	};
}

const server = new McpServer({
	name: "mcp-pricing",
	version: "0.2.0",
});

server.tool(
	"predict_resale",
	"Run resale prediction for listing_id",
	{
		listing_id: z.string().min(1),
	},
	async ({ listing_id }) => {
		try {
			const result = await apiPost(`/predict/resale/${encodeURIComponent(listing_id)}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "predict_resale_failed");
		}
	}
);

server.tool(
	"predict_auction",
	"Run auction prediction for listing_id",
	{
		listing_id: z.string().min(1),
	},
	async ({ listing_id }) => {
		try {
			const result = await apiPost(`/predict/auction/${encodeURIComponent(listing_id)}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "predict_auction_failed");
		}
	}
);

server.tool(
	"score_listing",
	"Rescore one listing and return listing summary",
	{
		listing_id: z.string().min(1),
	},
	async ({ listing_id }) => {
		try {
			const result = await apiPost(`/score/${encodeURIComponent(listing_id)}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "score_listing_failed");
		}
	}
);

server.tool(
	"run_retrain_job",
	"Run baseline retrain pipeline",
	{},
	async () => {
		try {
			const result = await apiPost(`/retrain/jobs`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "run_retrain_job_failed");
		}
	}
);

const transport = new StdioServerTransport();
await server.connect(transport);
