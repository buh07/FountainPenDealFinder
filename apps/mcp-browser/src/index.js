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
		sort_by: z.enum(["risk_adjusted", "flat_profit", "percent_profit"]).optional(),
		listing_type: z.enum(["auction", "buy_now"]).optional(),
		since_hours: z.number().int().min(1).max(24 * 14).optional(),
		ending_within_hours: z.number().int().min(1).max(72).optional(),
		limit: z.number().int().min(1).max(200).optional(),
		offset: z.number().int().min(0).max(100000).optional(),
	},
	async ({
		source,
		bucket,
		sort_by = "risk_adjusted",
		listing_type,
		since_hours,
		ending_within_hours,
		limit = 25,
		offset = 0,
	}) => {
		try {
			const params = new URLSearchParams();
			if (source) params.set("source", source);
			if (bucket) params.set("bucket", bucket);
			if (sort_by) params.set("sort_by", sort_by);
			if (listing_type) params.set("listing_type", listing_type);
			if (since_hours) params.set("since_hours", String(since_hours));
			if (ending_within_hours) params.set("ending_within_hours", String(ending_within_hours));
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
	"get_listing_images",
	"Fetch listing image URLs and captured image assets",
	{
		listing_id: z.string().min(1),
		include_assets: z.boolean().optional(),
	},
	async ({ listing_id, include_assets = true }) => {
		try {
			const params = new URLSearchParams();
			params.set("include_assets", include_assets ? "true" : "false");
			const result = await apiGet(`/listings/${encodeURIComponent(listing_id)}/images?${params.toString()}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "get_listing_images_failed");
		}
	}
);

server.tool(
	"get_new_listings",
	"Fetch recent ranked listings in the last N hours",
	{
		since_hours: z.number().int().min(1).max(24 * 14).default(24),
		source: z.string().optional(),
		bucket: z.enum(["confident", "potential", "discard"]).optional(),
		limit: z.number().int().min(1).max(200).optional(),
	},
	async ({ since_hours = 24, source, bucket, limit = 50 }) => {
		try {
			const params = new URLSearchParams();
			params.set("since_hours", String(since_hours));
			params.set("limit", String(limit));
			params.set("offset", "0");
			if (source) params.set("source", source);
			if (bucket) params.set("bucket", bucket);
			const result = await apiGet(`/listings?${params.toString()}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "get_new_listings_failed");
		}
	}
);

server.tool(
	"get_ending_auctions",
	"Fetch auction listings ending within the next N hours",
	{
		window_hours: z.number().int().min(1).max(72).default(24),
		source: z.string().optional(),
		limit: z.number().int().min(1).max(200).optional(),
	},
	async ({ window_hours = 24, source, limit = 50 }) => {
		try {
			const params = new URLSearchParams();
			params.set("listing_type", "auction");
			params.set("ending_within_hours", String(window_hours));
			params.set("limit", String(limit));
			params.set("offset", "0");
			if (source) params.set("source", source);
			const result = await apiGet(`/listings?${params.toString()}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "get_ending_auctions_failed");
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
