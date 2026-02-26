const MCP_URL_DEFAULT = "https://geomanic.com/api/v1/mcp";

let requestId = 1;

async function callMcp(toolName, args, config) {
  const url = config.apiUrl || MCP_URL_DEFAULT;

  const body = {
    jsonrpc: "2.0",
    id: requestId++,
    method: "tools/call",
    params: { name: toolName, arguments: args },
  };

  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${config.apiKey}`,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Geomanic API error (HTTP ${res.status}): ${text}`);
  }

  const json = await res.json();

  if (json.error) {
    throw new Error(json.error.message || JSON.stringify(json.error));
  }

  const content = json.result?.content;
  if (content && content.length > 0 && content[0].text) {
    try {
      return JSON.parse(content[0].text);
    } catch {
      return { result: content[0].text };
    }
  }

  return json.result || json;
}

export default {
  async create_waypoint(params, { config }) {
    return callMcp("create_waypoint", params, config);
  },

  async update_waypoint(params, { config }) {
    return callMcp("update_waypoint", params, config);
  },

  async delete_waypoint(params, { config }) {
    return callMcp("delete_waypoint", params, config);
  },

  async get_waypoint(params, { config }) {
    return callMcp("get_waypoint", params, config);
  },

  async list_waypoints(params, { config }) {
    return callMcp("list_waypoints", params, config);
  },

  async get_statistics(params, { config }) {
    return callMcp("get_statistics", params, config);
  },

  async get_date_range(params, { config }) {
    return callMcp("get_date_range", params, config);
  },
};
