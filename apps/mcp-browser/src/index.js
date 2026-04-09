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
	name: "mcp-browser",
	version: "0.2.0",
});

server.tool(
	"search_listings",
	"Search ranked listings from internal API",
	{
		source: z.string().optional(),
		bucket: z.enum(["confident", "potential", "discard"]).optional(),
		limit: z.number().int().min(1).max(200).optional(),
		offset: z.number().int().min(0).max(100000).optional(),
	},
	async ({ source, bucket, limit = 25, offset = 0 }) => {
		try {
			const params = new URLSearchParams();
			if (source) params.set("source", source);
			if (bucket) params.set("bucket", bucket);
			params.set("limit", String(limit));
			params.set("offset", String(offset));
			const result = await apiGet(`/listings?${params.toString()}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "search_listings_failed");
		}
	}
);

server.tool(
	"get_listing_detail",
	"Fetch one listing summary by listing_id",
	{
		listing_id: z.string().min(1),
	},
	async ({ listing_id }) => {
		try {
			const result = await apiGet(`/listings/${encodeURIComponent(listing_id)}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "get_listing_detail_failed");
		}
	}
);

server.tool(
	"refresh_ending_auctions",
	"Trigger ending-auctions refresh job",
	{
		window_hours: z.number().int().min(1).max(72).optional(),
	},
	async ({ window_hours = 24 }) => {
		try {
			const result = await apiPost(`/collect/refresh-ending?window_hours=${encodeURIComponent(String(window_hours))}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "refresh_ending_auctions_failed");
		}
	}
);

const transport = new StdioServerTransport();
await server.connect(transport);
