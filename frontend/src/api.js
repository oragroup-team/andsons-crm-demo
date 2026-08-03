const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:5001";

async function post(path, body) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed with status ${res.status}`);
  }
  return data;
}

export function generateEmail(flowName, firstName) {
  return post("/generate-email", { flow_name: flowName, first_name: firstName });
}

export function reviseEmail(flowName, firstName, feedback, previousRenderedText, feedbackHistory) {
  return post("/revise-email", {
    flow_name: flowName,
    first_name: firstName,
    feedback,
    previous_rendered_text: previousRenderedText,
    feedback_history: feedbackHistory || [],
  });
}

export function askQuestion(question) {
  return post("/ask", { question });
}
