// Mirrors backend/flows.py — the real andSons Hair Loss lifecycle flows,
// in Thalia's staging priority order (2026-07-06).
export const FLOWS = [
  { value: "p1_plan_not_purchased", label: "P1 - Plan Created, Not Purchased" },
  { value: "p2_consult_no_show", label: "P2 - Consult No-Show" },
  { value: "p3_otc_cart_abandon", label: "P3 - OTC Cart Abandon" },
  { value: "the_valley", label: "The Valley (Month 1-3)" },
  { value: "consult_booking", label: "Consult Booking" },
  { value: "replenishment_dunning", label: "Replenishment / Dunning" },
  { value: "rx_not_suitable_otc", label: "Rx Not Suitable -> OTC" },
  { value: "aov_growth", label: "AOV Growth" },
  { value: "winback", label: "Win-back" },
  { value: "results_milestone", label: "Results & Milestone (Month 4+)" },
  { value: "quiz_recovery", label: "Quiz Recovery" },
];
