import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const API_BASE = process.env.MCP_API_BASE || "http://localhost:8000";

function asErrorEnvelope(code, message, details = null) {
	return JSON.stringify({ ok: false, code, message, details });
}

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
	name: "mcp-deal-scoring",
	version: "0.2.0",
});

server.tool(
	"score_listing",
	"Recompute full deal score for listing_id",
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
	"ranked_deals",
	"Fetch ranked deals by selected ranking view",
	{
		sort_by: z.enum(["risk_adjusted", "flat_profit", "percent_profit"]).optional(),
		bucket: z.enum(["confident", "potential", "discard"]).optional(),
		limit: z.number().int().min(1).max(200).optional(),
		offset: z.number().int().min(0).max(100000).optional(),
	},
	async ({ sort_by = "risk_adjusted", bucket, limit = 50, offset = 0 }) => {
		try {
			const params = new URLSearchParams();
			params.set("sort_by", sort_by);
			params.set("limit", String(limit));
			params.set("offset", String(offset));
			if (bucket) params.set("bucket", bucket);
			const result = await apiGet(`/listings?${params.toString()}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "ranked_deals_failed");
		}
	}
);

server.tool(
	"generate_daily_report",
	"Fetch or generate daily report by date and ranking view",
	{
		report_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
		sort_by: z.enum(["risk_adjusted", "flat_profit", "percent_profit"]).optional(),
	},
	async ({ report_date, sort_by = "risk_adjusted" }) => {
		try {
			const result = await apiGet(`/reports/daily/${encodeURIComponent(report_date)}?sort_by=${encodeURIComponent(sort_by)}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "generate_daily_report_failed");
		}
	}
);

const transport = new StdioServerTransport();
await server.connect(transport);
