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
	name: "mcp-classification",
	version: "0.2.0",
});

server.tool(
	"get_taxonomy_standard",
	"Fetch canonical pen taxonomy, condition taxonomy, and damage flags",
	{},
	async () => {
		try {
			const result = await apiGet("/taxonomy/standard");
			return okContent(result);
		} catch (error) {
			return errorContent(error, "get_taxonomy_standard_failed");
		}
	}
);

server.tool(
	"rescore_listing_classification",
	"Re-run scoring/classification pipeline for a listing",
	{
		listing_id: z.string().min(1),
	},
	async ({ listing_id }) => {
		try {
			const result = await apiPost(`/score/${encodeURIComponent(listing_id)}`);
			return okContent(result);
		} catch (error) {
			return errorContent(error, "rescore_listing_classification_failed");
		}
	}
);

server.tool(
	"submit_manual_review",
	"Submit manual taxonomy/condition feedback for one listing",
	{
		listing_id: z.string().min(1),
		action_type: z.enum([
			"confirm_classification",
			"correct_classification",
			"add_new_type",
			"mark_fake_suspicious",
			"mark_condition_worse",
			"mark_purchased",
			"mark_sold_too_fast",
			"mark_not_worth_it",
		]),
		corrected_classification_id: z.string().optional(),
		corrected_brand: z.string().optional(),
		corrected_line: z.string().optional(),
		corrected_condition_grade: z.string().optional(),
		corrected_item_count: z.number().int().min(1).optional(),
		notes: z.string().optional(),
		reviewer: z.string().optional(),
	},
	async ({
		listing_id,
		action_type,
		corrected_classification_id,
		corrected_brand,
		corrected_line,
		corrected_condition_grade,
		corrected_item_count,
		notes = "",
		reviewer = "mcp",
	}) => {
		try {
			const result = await apiPost(`/review/${encodeURIComponent(listing_id)}`, {
				action_type,
				corrected_classification_id: corrected_classification_id ?? null,
				corrected_brand: corrected_brand ?? null,
				corrected_line: corrected_line ?? null,
				corrected_condition_grade: corrected_condition_grade ?? null,
				corrected_item_count: corrected_item_count ?? null,
				notes,
				reviewer,
			});
			return okContent(result);
		} catch (error) {
			return errorContent(error, "submit_manual_review_failed");
		}
	}
);

const transport = new StdioServerTransport();
await server.connect(transport);
