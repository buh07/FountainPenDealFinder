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
	name: "mcp-proxy",
	version: "0.2.0",
});

server.tool(
	"get_proxy_deals",
	"Fetch all proxy/coupon options for one listing",
	{
		listing_id: z.string().min(1),
	},
	async ({ listing_id }) => {
		try {
			const result = await apiGet(`/proxy/listing/${encodeURIComponent(listing_id)}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "get_proxy_deals_failed");
		}
	}
);

server.tool(
	"get_top_proxy_deals",
	"Fetch top proxy opportunities ranked by expected profit",
	{
		proxy_name: z.string().optional(),
		limit: z.number().int().min(1).max(200).optional(),
	},
	async ({ proxy_name, limit = 50 }) => {
		try {
			const params = new URLSearchParams();
			params.set("limit", String(limit));
			if (proxy_name) params.set("proxy_name", proxy_name);
			const result = await apiGet(`/proxy/top?${params.toString()}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "get_top_proxy_deals_failed");
		}
	}
);

const transport = new StdioServerTransport();
await server.connect(transport);
