"""Analytics chat agent - LangChain SQL agent over the live ORA BigQuery
warehouse, plus real MoEngage campaign/engagement data when it's actually
relevant to the question. MoEngage retrieval mechanism: moengage_dump_
context.py - reads moengage_export/daily_flow_tracker.py's own real daily
output (a fresh full-account pull, run once a day at 06:00 SGT by the
/cron/daily-flow-tracker endpoint, not per question anymore - see that
module's own docstring for the real architecture and the earlier per-
question-dump version's cost this replaced) plus its accumulated multi-day
history file for genuine trend/periodic context, not just one day's
snapshot. This REPLACES the chart-catalog mechanism (moengage_summary.py)
that used to be imported here - moengage_summary.py itself is untouched,
still fully intact, and still actively used by agents/insight_agent.py;
it's simply no longer called from this file. Kept in place rather than
deleted so it's a one-line import swap back if this mechanism turns out to
be a problem in practice.

The agent must NEVER state a number in its final answer that didn't come
from an actual query result (or, for MoEngage, an actual flow-dump value).
Enforced with a system-prompt instruction PLUS a post-hoc check: every
number in the final answer is confirmed to appear somewhere in this run's
tool (query) results, uploaded file, or MoEngage context; if any number
can't be traced, the answer is replaced with an explicit "I couldn't
verify that figure".

The SQL query actually executed is captured and returned alongside the
answer so it can be shown in the demo. `moengage_used` in the return value
reports whether MoEngage context was actually pulled in for this answer
(most questions won't need it - it's only fetched/folded in when relevant).
"""
import functools
import logging
import os
import re
from datetime import date
from typing import Literal, Optional

from langchain_community.agent_toolkits.sql.base import create_sql_agent
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from moengage_dump_context import gather_moengage_context  # see module docstring above: replaces moengage_summary.py's
# chart-catalog mechanism for THIS agent only - moengage_summary.py itself is untouched, still used by insight_agent.py.
from text_sanitize import sanitize_text

from .llm_provider import get_llm

logger = logging.getLogger("analytics_agent")

BIGQUERY_SCHEMA_NOTES = """RAW SCHEMA - every real table this agent can query, every real column, as returned directly by BigQuery's own INFORMATION_SCHEMA.COLUMNS (live-pulled, not summarized or reinterpreted). Real, live-verified business-meaning notes ARE included further below, after the raw dump - use them, but also verify against real DISTINCT values yourself per the mandatory process when it matters, since the underlying data can drift after these were written. Two real projects are involved:

- ora-bigquery.ora_bigquery_pipeline: the connected project. The schema-inspection tool also works here as a second way to look up the same columns.
- crm-mail-automation-dev.crm_analytics_views: a DIFFERENT project. The schema-inspection tool CANNOT see these tables at all - that is not a sign they do not exist. Query `crm-mail-automation-dev.crm_analytics_views.INFORMATION_SCHEMA.COLUMNS` yourself, or use the dump below, then query the table directly by its full three-part name (project.dataset.table).

=== ora-bigquery.ora_bigquery_pipeline (connected project - schema-inspection tool also works here) ===

REAL MOENGAGE FLOW/NODE-LEVEL DATA, 7 real tables, one PER BRAND (added 2026-09-28 - use the schema-inspection \
tool on any of these for exact columns, same as any other table here): moengage_daily_flow_tracker_AS_SG, \
moengage_daily_flow_tracker_AS_MY, moengage_daily_flow_tracker_AS_PH, moengage_daily_flow_tracker_OVA_SG, \
moengage_daily_flow_tracker_OVA_MY, moengage_daily_flow_tracker_OVA_PH, moengage_daily_flow_tracker_MODERN_MOLECULES \
- one real row per (flow, send node, day), covering every real MoEngage flow in that brand's own workspace, \
every real status, no filtering. Real columns include attempted/sent/delivered/opened/clicked/conversions/ \
revenue PER SEND NODE (e.g. a specific WhatsApp or Email step inside a flow), date_range_start/date_range_end \
(the real day this row covers), flow_name, node_label, channel, variation (all_variations vs control_group). \
Real, current date coverage: 2026-09-08/09 through 2026-09-26, growing daily via a real cron pull - a question \
about a date outside that range has no real data here yet, say so rather than guessing. USE THIS for any \
question about a SPECIFIC MoEngage flow's own real send/open/click/delivery performance, or a real trend in \
that flow's own numbers over time, PER BRAND (each brand's own workspace is a separate real table - "ASSG" \
means the moengage_daily_flow_tracker_AS_SG table specifically, "Ova MY" means moengage_daily_flow_tracker_ \
OVA_MY, etc. - never blend two brands' tables into one answer unless the question genuinely asks for a \
cross-brand comparison, and if it does, state each brand's own number separately, never a summed total that \
hides which brand drove it). This is DIFFERENT from and a REAL, separate source from flow_orders/ \
dotcom_plus_marketplace (real completed ORDERS, not send/open/click events) and the moengage_campaigns_* \
tables in the other project below (real per-CAMPAIGN aggregate stats, not per-flow-node) - prefer THIS table \
group specifically when the question is about a named flow's own send-level behavior (e.g. "how many opens \
did the abandoned cart WhatsApp message get") that those other two groups don't carry at this grain.

TABLE dotcom_plus_marketplace (40 columns):
Country STRING, Brand STRING, Channel STRING, order_id STRING, status STRING, created_at DATETIME, clean_sku STRING, Revenue_Type STRING, Cleaned_Revenue_Type STRING, quantity INT64, product_category STRING, new_product_category STRING, Prescription_Type STRING, Revenue FLOAT64, Final_Revenue FLOAT64, New_COGS FLOAT64, COGS_Less_RND FLOAT64, New_COGS_LCY FLOAT64, COGS_LCY_Less_RND FLOAT64, Order_Baskets INT64, Credit_Card_Expense FLOAT64, Delivery_Fee FLOAT64, Order_Type STRING, Applicable_Discount FLOAT64, Applicable_Cashback FLOAT64, Preponed STRING, Num_Orderlines_AV INT64, revenue_reporting_date DATETIME, sku_for_wms STRING, AV_GM2_Expenses FLOAT64, GM2_expenses FLOAT64, Seller_Discount_USD FLOAT64, Platform_Discount_USD FLOAT64, sku STRING, New_Quantity FLOAT64, Commission_Fee FLOAT64, Transaction_Fee FLOAT64, Service_Fee FLOAT64, Marketing_Fee FLOAT64, Selling_Price_USD FLOAT64

TABLE updated_sales_data (97 columns):
order_id INT64, cart_id FLOAT64, status STRING, user_id INT64, product_option_price_id INT64, sku STRING, sku_for_wms STRING, quantity INT64, subscription_id FLOAT64, created_at DATETIME, discount_id FLOAT64, discount_total_amount FLOAT64, orders_utm_source STRING, orders_utm_medium STRING, orders_utm_campaign STRING, orders_utm_term STRING, tp_source STRING, order_platform STRING, Country STRING, Brand STRING, email STRING, trigger_source STRING, payment_method_type STRING, product_category STRING, sku_quantity FLOAT64, Months_Of_Products FLOAT64, Revenue FLOAT64, new_product_category STRING, Prescription_Type STRING, code STRING, Applicable_Discount FLOAT64, Applicable_Cashback FLOAT64, Final_Revenue FLOAT64, Applicable_Refund FLOAT64, Num_Orderlines_AV INT64, Applicable_Shipping_Fee FLOAT64, phone STRING, signup_utm_campaign STRING, signup_utm_term STRING, signup_timestamp DATETIME, signup_category STRING, signup_platform STRING, signup_utm_source STRING, signup_utm_medium STRING, UNC_Check STRING, UNC INT64, Order_Baskets INT64, Order_Type STRING, New_Customer_Revenue FLOAT64, New_Customer_Orderlines INT64, counter FLOAT64, age FLOAT64, First_Age FLOAT64, First_Age_Bucket STRING, first_order_type STRING, first_order_type_clean STRING, Cohort_First_Month STRING, sku_of_first_order STRING, Cat_Of_First_Order STRING, Preponed STRING, discount_offering_id FLOAT64, discount_amount FLOAT64, Product_Driven_Orderlines FLOAT64, source STRING, Product_Driven_Type STRING, is_buy_now_cart FLOAT64, is_plan_changed FLOAT64, plan_changed_by STRING, transaction_type STRING, cod_status STRING, order_number INT64, Payment_Reference STRING, Revenue_Type STRING, Nomenclature STRING, First_SKU_Nomenclature STRING, Active_Price FLOAT64, New_Active_Price FLOAT64, Sub_Discount FLOAT64, Willingness_To_Pay_Customers STRING, Final_Sub_Discount FLOAT64, sku_for_pop_id_mapping STRING, product_option_price_id2 FLOAT64, COGS_LCY FLOAT64, New_COGS FLOAT64, New_COGS_LCY FLOAT64, COGS_Less_RND FLOAT64, COGS_LCY_Less_RND FLOAT64, Cleaned_Revenue_Type STRING, Credit_Card_Expense FLOAT64, Delivery_Fee FLOAT64, Month_Num INT64, Year INT64, Month_Name STRING, Preponed_Label STRING, Preponed_Date DATE, Clean_Next_Payment_Date DATE, revenue_reporting_date DATETIME

TABLE marketing_spend_data (12 columns):
Country STRING, Brand STRING, Channel STRING, Year INT64, Month_Num INT64, Month_Name STRING, Date DATETIME, Spends FLOAT64, Clicks STRING, Impressions STRING, Classification STRING, Category STRING

TABLE sg_regs_funnel (18 columns):
user_id STRING, phone STRING, created_at TIMESTAMP, utm_campaign_signups STRING, utm_term_signups STRING, signup_category STRING, utm_source_signups STRING, utm_medium_signups STRING, Context STRING, new_product_category STRING, Flow STRING, instance_id INT64, bmi FLOAT64, appt_date_time TIMESTAMP, appt_created_at TIMESTAMP, first_order_timestamp TIMESTAMP, Country STRING, Brand STRING

TABLE user_level_funnel (20 columns):
instance_id STRING, user_id STRING, created_at STRING, provider STRING, utm_source_signups STRING, utm_medium_signups STRING, utm_campaign_signups STRING, utm_term_signups STRING, signup_category STRING, new_product_category STRING, UNC STRING, Context STRING, bmi STRING, utm_source_bmi STRING, utm_medium_bmi STRING, utm_campaign_bmi STRING, utm_term_bmi STRING, Country STRING, Brand STRING, Semaglutide_Checker STRING

TABLE latest_funnel_view (20 columns):
user_id STRING, phone STRING, created_at DATETIME, utm_campaign_signups STRING, utm_term_signups STRING, signup_category STRING, utm_source_signups STRING, utm_medium_signups STRING, Context STRING, new_product_category STRING, Flow STRING, instance_id INT64, Is_Normal_Consult FLOAT64, Journey_Stage STRING, Journey_Stage_Rank FLOAT64, appt_date_time DATETIME, appt_created_at DATETIME, first_order_timestamp DATETIME, Country STRING, Brand STRING

TABLE consult_cats_sg_regs_funnel (18 columns):
user_id STRING, phone STRING, created_at TIMESTAMP, utm_campaign_signups STRING, utm_term_signups STRING, signup_category STRING, utm_source_signups STRING, utm_medium_signups STRING, Context STRING, new_product_category STRING, Flow STRING, instance_id INT64, bmi FLOAT64, appt_date_time TIMESTAMP, appt_created_at TIMESTAMP, first_order_timestamp TIMESTAMP, Country STRING, Brand STRING

TABLE sg_regs_funnel_with_cohort_view (21 columns):
user_id STRING, phone STRING, created_at DATETIME, utm_campaign_signups STRING, utm_term_signups STRING, signup_category STRING, utm_source_signups STRING, utm_medium_signups STRING, Context STRING, new_product_category STRING, Flow STRING, instance_id INT64, bmi FLOAT64, Cohort_Status STRING, Final_Cohort_Status_Marker FLOAT64, Final_Cohort_Status_2_Marker FLOAT64, appt_date_time DATETIME, appt_created_at DATETIME, first_order_timestamp DATETIME, Country STRING, Brand STRING

TABLE ATC_aggregated (11 columns):
created_at DATE, brand STRING, country STRING, category_name STRING, total_same_day_signups INT64, total_unique_users INT64, total_users_rx INT64, total_users_with_appt INT64, total_final_orders INT64, total_final_orders_rx INT64, new_category_name STRING

TABLE Signups_ATC_Funnel (8 columns):
Country STRING, Brand STRING, user_category_name STRING, signup_date DATE, total_unique_users INT64, same_day_carts INT64, total_carts INT64, total_orders INT64

TABLE automation_testing_ova_sg_funnel (22 columns):
instance_id STRING, user_id STRING, answer STRING, created_at STRING, provider STRING, utm_source_signups STRING, utm_medium_signups STRING, utm_campaign_signups STRING, utm_term_signups STRING, signup_category STRING, new_product_category STRING, UNC STRING, Context STRING, source STRING, bmi STRING, utm_source_bmi STRING, utm_medium_bmi STRING, utm_campaign_bmi STRING, utm_term_bmi STRING, Purchaser_Country STRING, Country STRING, Brand STRING

TABLE marketing_data (21 columns):
day DATE, campaign_name STRING, impressions INT64, clicks INT64, category STRING, campaign_type STRING, campaign_type_clean STRING, country STRING, brand STRING, spends FLOAT64, order_level_nur FLOAT64, signup_level_nur FLOAT64, channel STRING, order_level_unc INT64, signup_level_unc INT64, signups INT64, order_level_total_revenue FLOAT64, signup_level_total_revenue FLOAT64, dotcom_vs_marketplace STRING, source STRING, load_ts TIMESTAMP

TABLE marketing_clicks_data (12 columns):
Country STRING, Brand STRING, Channel STRING, Year INT64, Month_Num INT64, Month_Name STRING, Date DATETIME, Search STRING, PMax STRING, Total_Clicks INT64, Classification STRING, Category STRING

TABLE marketing_historical_data (11 columns):
Country STRING, Brand STRING, Adset STRING, Date DATETIME, Category STRING, Campaign_Type STRING, Campaign_Type_Clean STRING, Spends FLOAT64, Total_Clicks STRING, Impressions STRING, Channel STRING

TABLE _staging_consolidated_paid_performance (21 columns):
day DATE, campaign_name STRING, impressions INT64, clicks INT64, category STRING, campaign_type STRING, campaign_type_clean STRING, country STRING, brand STRING, spends FLOAT64, order_level_nur FLOAT64, signup_level_nur FLOAT64, channel STRING, order_level_unc INT64, signup_level_unc INT64, signups INT64, order_level_total_revenue FLOAT64, signup_level_total_revenue FLOAT64, dotcom_vs_marketplace STRING, source STRING, load_ts TIMESTAMP

TABLE creative_data (24 columns):
campaign_name STRING, impressions INT64, link_clicks FLOAT64, amount_spent FLOAT64, account_name STRING, day DATETIME, ad_name STRING, cid_name STRING, category STRING, campaign_type STRING, campaign_type_clean STRING, mm_country_temp STRING, brand STRING, country STRING, spends FLOAT64, order_level_nur FLOAT64, signup_level_nur FLOAT64, channel STRING, order_level_unc FLOAT64, signup_level_unc FLOAT64, signups FLOAT64, order_level_total_revenue FLOAT64, signup_level_total_revenue FLOAT64, dotcom_v_s_marketplace STRING

TABLE marketplace_marketing_spends (8 columns):
Date DATETIME, Spends INT64, UNC INT64, Traffic STRING, Country STRING, Brand STRING, Channel STRING, Category STRING

TABLE doctor_consultation_sg (42 columns):
consult_id INT64, order_id INT64, user_id FLOAT64, consult_status STRING, consult_reason STRING, rx_id FLOAT64, rx_status STRING, subs_status FLOAT64, cancel_reason STRING, cancelled_at DATETIME, consult_day STRING, consult_day_with_number STRING, order_status STRING, reconsult INT64, bmi FLOAT64, subs_order_id FLOAT64, subs_order_status STRING, subs_order_created_at DATETIME, subs_order_updated_at DATETIME, order_update_flag STRING, charge_tp_button_clicked INT64, charge_tp_success INT64, reason_type STRING, reason STRING, consult_date DATETIME, consult_source STRING, dr_email STRING, subscription_id FLOAT64, product_id FLOAT64, latest_sku STRING, category_name STRING, cart_cat_name STRING, first_order_category STRING, final_category STRING, utm_campaign STRING, user_email STRING, name STRING, age FLOAT64, gender STRING, ATTENDED_DR STRING, brand STRING, country STRING

TABLE doctor_consultation_all (44 columns):
consult_id INT64, order_id INT64, user_id INT64, consult_status STRING, consult_reason STRING, rx_id FLOAT64, rx_status STRING, subs_status FLOAT64, cancel_reason STRING, cancelled_at DATETIME, consult_day STRING, consult_day_with_number STRING, order_status STRING, reconsult INT64, bmi FLOAT64, subs_order_id FLOAT64, subs_order_status STRING, subs_order_created_at DATETIME, subs_order_updated_at DATETIME, order_update_flag STRING, charge_tp_button_clicked INT64, charge_tp_success INT64, reason_type STRING, reason STRING, consult_date DATETIME, consult_source STRING, dr_email STRING, subscription_id FLOAT64, product_id FLOAT64, latest_sku STRING, ori_sku STRING, final_sku STRING, category_name STRING, cart_cat_name STRING, first_order_category STRING, final_category STRING, utm_campaign STRING, user_email STRING, name STRING, age FLOAT64, gender STRING, ATTENDED_DR STRING, brand STRING, country STRING

TABLE doctor_consultation_my (43 columns):
consult_id INT64, order_id INT64, user_id FLOAT64, consult_status STRING, consult_reason STRING, rx_id FLOAT64, rx_status STRING, subs_status FLOAT64, cancel_reason STRING, cancelled_at DATETIME, consult_day STRING, consult_day_with_number STRING, order_status STRING, reconsult INT64, bmi FLOAT64, subs_order_id FLOAT64, subs_order_status STRING, subs_order_created_at DATETIME, subs_order_updated_at DATETIME, order_update_flag STRING, charge_tp_button_clicked INT64, charge_tp_success INT64, reason_type STRING, reason STRING, consult_date DATETIME, consult_source STRING, dr_email STRING, subscription_id FLOAT64, product_id FLOAT64, latest_sku STRING, category_name STRING, cart_cat_name STRING, first_order_category STRING, final_category STRING, utm_campaign STRING, user_email STRING, name STRING, dob DATE, age FLOAT64, gender STRING, ATTENDED_DR STRING, brand STRING, country STRING

TABLE order_to_consultation_my (15 columns):
trigger_source STRING, order_id INT64, cart_id INT64, dr_email STRING, attended_dr STRING, name STRING, reconsultation INT64, user_id INT64, cart_created DATETIME, order_created DATETIME, consult_datetime DATETIME, adjusted_order_to_consult_hours FLOAT64, adjusted_consult_time_bucket STRING, brand STRING, country STRING

TABLE customer_analysis_data (42 columns):
Country STRING, age FLOAT64, Cohort_First_Month STRING, sku_of_first_order STRING, Cat_Of_First_Order STRING, Brand2 STRING, Full_Phone STRING, signup_timestamp STRING, Num_Purchases_Order_ID FLOAT64, Num_Categories FLOAT64, First_Non_Consult_SKU_Purchase_Date STRING, Second_Non_Consult_SKU_Purchase_Date STRING, Third_Non_Consult_SKU_Purchase_Date STRING, First_Non_Consult_Cat_Purchase_Date STRING, Second_Non_Consult_Cat_Purchase_Date STRING, Third_Non_Consult_Cat_Purchase_Date STRING, First_Purchase_Date STRING, First_Non_Consult_Purchase_Date STRING, literal_sku_of_first_order STRING, First_Non_Consult_Purchase_Type STRING, Ever_Bought_Subs STRING, Second_Purchase_Date STRING, Second_Non_Consult_Purchase_Date STRING, First_EC_Purchase_Date STRING, First_BC_Purchase_Date STRING, First_ED_Purchase_Date STRING, First_PE_Purchase_Date STRING, First_HL_Purchase_Date STRING, First_Well_Being_Purchase_Date STRING, First_Weight_Loss_Purchase_Date STRING, First_Weight_Loss_Program_Purchase_Date STRING, First_Period_Delay_Purchase_Date STRING, First_SC_Purchase_Date STRING, Detailed_First_Non_Consult_Purchase_Type STRING, First_OneOff_Purchase_Date STRING, First_1Msub_Purchase_Date STRING, First_3Msub_Purchase_Date STRING, First_6Msub_Purchase_Date STRING, Date_Of_UNC DATETIME, Last_Purchase_Date DATETIME, Num_Purchases_Order_Baskets FLOAT64, Num_NC_Categories FLOAT64

TABLE utm_cohort_data (27 columns):
Country STRING, Brand STRING, Cat_Of_First_Order STRING, Cohort_First_Month INT64, Cohort_Order_Month INT64, utm STRING, Acquisition_Product_Type STRING, Cohort_Size FLOAT64, Customers INT64, Orders INT64, Revenue INT64, Actionable_Weighted_Average FLOAT64, Actionable_Weighted_Average_Revenue FLOAT64, Customer_Projection FLOAT64, Revenue_Projection FLOAT64, Repeat_Customer_Actuals FLOAT64, Repeat_Revenue_Actuals FLOAT64, Percentage_Return_Rate FLOAT64, Percentage_Return_Rate_Revenue FLOAT64, Month_Of_Acquisition STRING, Days_Elapsed INT64, Days_In_Month INT64, Current_Month STRING, Latest_Month STRING, Cohort_Ref STRING, Extrapolated_Customer_Actuals FLOAT64, Extrapolated_Revenue_Actuals FLOAT64

TABLE subs_only_cohort_data (20 columns):
Country STRING, Brand STRING, Cat_Of_First_Order STRING, Cohort_First_Month INT64, Cohort_Order_Month INT64, Cohort_Size FLOAT64, Cohort_Size_Revenue FLOAT64, Customers INT64, Orders INT64, Revenue INT64, Actionable_Weighted_Average FLOAT64, Weighted_Average_Cumulative_Orders FLOAT64, Percentage_Return_Rate FLOAT64, Percentage_Return_Rate_Revenue FLOAT64, ARPU FLOAT64, AOV FLOAT64, Cumulative_Orders_Per_Cohort_Member FLOAT64, Cumulative_Revenue_Per_Cohort_Member FLOAT64, Weighted_Average_ARPU FLOAT64, Month_Of_Acquisition STRING

TABLE signups_purchase_time (23 columns):
Full_Phone STRING, Country STRING, Brand STRING, signed_up_date STRING, Age INT64, First_Purchase_Date STRING, First_Non_Consult_Purchase_Date STRING, literal_sku_of_first_order STRING, sku_of_first_order STRING, Cat_Of_First_Order STRING, First_Non_Consult_Purchase_Type STRING, Ever_Bought_Subs STRING, Second_Purchase_Date STRING, Second_Non_Consult_Purchase_Date STRING, First_EC_Purchase_Date STRING, First_BC_Purchase_Date STRING, First_ED_Purchase_Date STRING, First_PE_Purchase_Date STRING, First_HL_Purchase_Date STRING, First_SUP_Purchase_Date STRING, First_WL_Purchase_Date STRING, First_WL_Program_Purchase_Date STRING, First_PD_Purchase_Date STRING

TABLE mom (19 columns):
order_id INT64, Order_Baskets INT64, created_at DATETIME, user_id INT64, phone STRING, new_product_category STRING, sku STRING, Country STRING, Brand STRING, Value FLOAT64, age FLOAT64, Prescription_Type STRING, Cohort_First_Month STRING, Cat_Of_First_Order STRING, sku_of_first_order STRING, status STRING, Order_Type STRING, Preponed STRING, Metric STRING

TABLE projection (16 columns):
year INT64, month_of_year STRING, Country STRING, Brand STRING, new_product_category STRING, Final_Revenue FLOAT64, New_Customer_Revenue FLOAT64, Total_Customers FLOAT64, New_Customers FLOAT64, Context STRING, Brand-Country STRING, Month_Number STRING, Day STRING, Date DATETIME, Repeat_Customer_Revenue FLOAT64, Repeat_Customers FLOAT64


=== crm-mail-automation-dev.crm_analytics_views (DIFFERENT project - schema-inspection tool CANNOT see these, query them directly by full name) ===

TABLE flow_orders (100 columns):
order_id INT64, cart_id FLOAT64, status STRING, user_id INT64, product_option_price_id INT64, sku STRING, sku_for_wms STRING, quantity INT64, subscription_id FLOAT64, created_at DATETIME, discount_id FLOAT64, discount_total_amount FLOAT64, orders_utm_source STRING, orders_utm_medium STRING, orders_utm_campaign STRING, orders_utm_term STRING, tp_source STRING, order_platform STRING, Country STRING, Brand STRING, email STRING, trigger_source STRING, payment_method_type STRING, product_category STRING, sku_quantity FLOAT64, Months_Of_Products FLOAT64, Revenue FLOAT64, new_product_category STRING, Prescription_Type STRING, code STRING, Applicable_Discount FLOAT64, Applicable_Cashback FLOAT64, Final_Revenue FLOAT64, Applicable_Refund FLOAT64, Num_Orderlines_AV INT64, Applicable_Shipping_Fee FLOAT64, phone STRING, signup_utm_campaign STRING, signup_utm_term STRING, signup_timestamp DATETIME, signup_category STRING, signup_platform STRING, signup_utm_source STRING, signup_utm_medium STRING, UNC_Check STRING, UNC INT64, Order_Baskets INT64, Order_Type STRING, New_Customer_Revenue FLOAT64, New_Customer_Orderlines INT64, counter FLOAT64, age FLOAT64, First_Age FLOAT64, First_Age_Bucket STRING, first_order_type STRING, first_order_type_clean STRING, Cohort_First_Month STRING, sku_of_first_order STRING, Cat_Of_First_Order STRING, Preponed STRING, discount_offering_id FLOAT64, discount_amount FLOAT64, Product_Driven_Orderlines FLOAT64, source STRING, Product_Driven_Type STRING, is_buy_now_cart FLOAT64, is_plan_changed FLOAT64, plan_changed_by STRING, transaction_type STRING, cod_status STRING, order_number INT64, Payment_Reference STRING, Revenue_Type STRING, Nomenclature STRING, First_SKU_Nomenclature STRING, Active_Price FLOAT64, New_Active_Price FLOAT64, Sub_Discount FLOAT64, Willingness_To_Pay_Customers STRING, Final_Sub_Discount FLOAT64, sku_for_pop_id_mapping STRING, product_option_price_id2 FLOAT64, COGS_LCY FLOAT64, New_COGS FLOAT64, New_COGS_LCY FLOAT64, COGS_Less_RND FLOAT64, COGS_LCY_Less_RND FLOAT64, Cleaned_Revenue_Type STRING, Credit_Card_Expense FLOAT64, Delivery_Fee FLOAT64, Month_Num INT64, Year INT64, Month_Name STRING, Preponed_Label STRING, Preponed_Date DATE, Clean_Next_Payment_Date DATE, revenue_reporting_date DATETIME, is_flow_attributed BOOL, is_excluded_status BOOL, flow_family STRING

TABLE moengage_flows_summary (55 columns):
Goal_2_In_Session_Global_Control_Group_CVR FLOAT64, Goal_1_In_Session_Global_Control_Group_CVR FLOAT64, Goal_2_In_Session_Control_Group_CVR FLOAT64, Goal_2_In_Session_Global_Control_Group_Uplift FLOAT64, Goal_1_In_Session_Global_Control_Group_Uplift FLOAT64, Goal_2_In_Session_Control_Group_Uplift FLOAT64, Goal_1_View_Through_Global_Control_Group_CVR FLOAT64, Custom_Segment_Filters STRING, Conversion_Goal_1_Value STRING, Goal_2_View_Through_Control_Group_CVR FLOAT64, Goal_1_View_Through_Control_Group_CVR FLOAT64, Goal_2_View_Through_Global_Control_Group_CVR FLOAT64, Goal_2_View_Through_Global_Control_Group_Uplift FLOAT64, Global_CG_enabled BOOL, Goal_1_Click_Through_Global_Control_Group_Uplift FLOAT64, Goal_1_View_Through_Global_Control_Group_Uplift FLOAT64, Goal_1_In_Session_Control_Group_Uplift FLOAT64, Conversion_Goal_2_Event STRING, Goal_2_View_Through_Control_Group_Uplift FLOAT64, Goal_1_View_Through_Control_Group_Uplift FLOAT64, Goal_2_Click_Through_Control_Group_CVR FLOAT64, Goal_1_Click_Through_Control_Group_CVR FLOAT64, Flow_Name STRING, Trips_Started_Total_users INT64, Flow_Status STRING, Conversion_Goal_1_Condition STRING, Goal_1_In_Session_Control_Group_CVR FLOAT64, Flow_Type STRING, Published_at TIMESTAMP, Conversion_Goal_2_Name STRING, Custom_Segment_Name STRING, Conversion_Goal_1_Name STRING, Campaign_Channel STRING, Control_Group_Stickiness_enabled BOOL, Trips_engaged INT64, Campaign_Control_Group_Percentage INT64, Tag_Category_Uncategorized STRING, Goal_2_Click_Through_Global_Control_Group_Uplift FLOAT64, Conversion_Goal_1_Attribute STRING, Conversion_Goal_2_Condition STRING, Conversion_Goal_2_Value STRING, Flow_Sent_Time TIMESTAMP, Goal_1_Click_Through_Control_Group_Uplift FLOAT64, Campaign_Delivery_Type STRING, Attribution_window INT64, Goal_2_Click_Through_Global_Control_Group_CVR FLOAT64, Conversion_Goal_2_Attribute STRING, Goal_1_Click_Through_Global_Control_Group_CVR FLOAT64, Conversion_Goal_1_Event STRING, Tag_Category_Default STRING, Goal_2_Click_Through_Control_Group_Uplift FLOAT64, Flow_Version_Name STRING, Trips_Started_GCG_Users INT64, Trips_Started_CG_Users INT64, Flows_Id STRING

TABLE moengage_campaigns_email (86 columns):
Goal_1_In_Session_Global_Control_Group_Conversions INT64, Goal_1_In_Session_Global_Control_Group_CVR INT64, Goal_1_In_Session_Control_Group_Uplift INT64, Goal_1_In_Session_CVR FLOAT64, Goal_1_In_Session_Total_Revenue FLOAT64, Goal_1_Click_Through_Global_Control_Group_CVR INT64, Goal_1_Click_Through_Total_Revenue FLOAT64, Goal_1_Click_Through_Conversion_Events INT64, Goal_1_Click_Through_Converted_Users INT64, Goal_1_Click_Through_Global_Control_Group_Uplift INT64, Goal_1_View_Through_Global_Control_Group_Uplift INT64, Goal_1_View_Through_Control_Group_Uplift INT64, Goal_1_View_Through_Control_Group_Conversions INT64, Goal_1_View_Through_Control_Group_CVR INT64, Goal_1_View_Through_CVR FLOAT64, Goal_1_In_Session_Global_Control_Group_Uplift INT64, Goal_1_View_Through_Total_Revenue FLOAT64, Goal_1_View_Through_Conversion_Events INT64, After_Duplicates_Removed INT64, After_B_U_C_Removed INT64, Goal_1_In_Session_Control_Group_Conversions INT64, Goal_1_Click_Through_Control_Group_Uplift INT64, Complaints INT64, Unsubscribe_rate FLOAT64, Goal_1_In_Session_Conversion_Events INT64, CTR FLOAT64, Total_Soft_bounces INT64, Open_rate FLOAT64, Delivery_rate FLOAT64, Complaints_rate FLOAT64, Campaign_Type STRING, Trigger_Delay STRING, Total_clicks INT64, Goal_1_Click_Through_Control_Group_Conversions INT64, Goal_1_View_Through_Converted_Users INT64, Soft_bounce_rate FLOAT64, Drops INT64, Delivered INT64, Total_Sent INT64, Hard_bounce_rate FLOAT64, Active_Target_Global_Control_Group INT64, Conversion_Goal_1_Name STRING, Sent INT64, Template_Name STRING, Campaign_Sending_Type STRING, Unique_clicks INT64, Goal_1_Click_Through_CVR FLOAT64, Campaign_Sent_Time STRING, From_Email_Address STRING, Active_Target_Control_Group INT64, Conversion_Goal_1_Condition STRING, Reply_To_Email_Address STRING, Goal_1_Click_Through_Global_Control_Group_Conversions INT64, Template_ID STRING, Tag_Category_Uncategorized STRING, Total_Hard_bounces INT64, Parent_Campaign_ID STRING, Email_Subject STRING, Campaign_Segment_Filters STRING, Total_Open INT64, Custom_Segment_Name STRING, Total_Delivered INT64, Flows_Name STRING, Conversion_Goal_1_Value STRING, Soft_bounces INT64, Campaign_ID STRING, Campaign_Status STRING, Goal_1_View_Through_Global_Control_Group_Conversions INT64, Campaign_Channel STRING, Campaign_CG INT64, Goal_1_In_Session_Control_Group_CVR INT64, Email_Attribute STRING, Goal_1_View_Through_Global_Control_Group_CVR INT64, After_Invalid_Emails_Removed INT64, Unsubscribes INT64, Unique_opens INT64, Created_By STRING, Goal_1_In_Session_Converted_Users INT64, Tag_Category_Default STRING, Campaign_Content_Type STRING, Goal_1_Click_Through_Control_Group_CVR INT64, Hard_bounces INT64, Campaign_Name STRING, Conversion_Goal_1_Attribute STRING, Campaign_Delivery_Type STRING, Conversion_Goal_1_Event STRING

TABLE moengage_campaigns_whatsapp (108 columns):
Goal_1_In_Session_Global_Control_Group_CVR INT64, Goal_2_In_Session_Control_Group_Conversions INT64, Goal_1_In_Session_CVR INT64, Goal_2_In_Session_Average_Order_Value INT64, Sent INT64, Goal_1_In_Session_Average_Order_Value INT64, Goal_1_In_Session_Total_Revenue INT64, Goal_1_In_Session_Global_Control_Group_Conversions INT64, Goal_2_In_Session_Total_Revenue INT64, Goal_2_In_Session_Conversion_Events INT64, Goal_1_View_Through_Global_Control_Group_CVR INT64, Read INT64, Goal_2_View_Through_Control_Group_Conversions INT64, Goal_1_Click_Through_Average_Order_Value INT64, Goal_1_View_Through_Control_Group_Conversions INT64, Goal_2_View_Through_Global_Control_Group_CVR INT64, Goal_1_View_Through_CVR FLOAT64, Goal_2_View_Through_Conversion_Events INT64, Goal_2_Click_Through_Total_Revenue INT64, Goal_1_Click_Through_Global_Control_Group_CVR INT64, Header STRING, Goal_2_Click_Through_Control_Group_Conversions INT64, Goal_1_Click_Through_CVR INT64, Goal_1_View_Through_Converted_Users INT64, Goal_2_Click_Through_Global_Control_Group_Conversions INT64, Goal_1_Click_Through_Control_Group_Conversions INT64, Goal_1_Click_Through_Global_Control_Group_Uplift INT64, Goal_2_Click_Through_Global_Control_Group_Uplift INT64, Goal_1_View_Through_Global_Control_Group_Uplift INT64, Goal_2_Click_Through_CVR INT64, Goal_1_View_Through_Total_Revenue FLOAT64, Goal_2_Click_Through_Average_Order_Value INT64, Campaign_Status STRING, Goal_1_Click_Through_Total_Revenue INT64, Goal_2_Click_Through_Conversion_Events INT64, Goal_1_Click_Through_Conversion_Events INT64, Tag_Category_Default STRING, Goal_1_View_Through_Average_Order_Value FLOAT64, Goal_1_View_Through_Conversion_Events INT64, Goal_1_Click_Through_Global_Control_Group_Conversions INT64, Whatsapp_Button_1_Action STRING, Goal_2_Click_Through_Converted_Users INT64, Goal_2_View_Through_Global_Control_Group_Conversions INT64, Goal_1_Click_Through_Converted_Users INT64, Goal_2_Revenue INT64, Footer STRING, Goal_2_CVR FLOAT64, CTOR FLOAT64, Goal_2_View_Through_Global_Control_Group_Uplift INT64, Campaign_Type STRING, Goal_2_In_Session_Global_Control_Group_Conversions INT64, Trigger_Delay STRING, Total_clicks INT64, Read_Rate FLOAT64, Delivered INT64, Total_Sent INT64, Goal_2_In_Session_Global_Control_Group_Uplift INT64, Conversion_Goal_1_Name STRING, Template_Name STRING, Goal_1_In_Session_Control_Group_Conversions INT64, Tag_Category_Uncategorized STRING, Campaign_Sending_Type STRING, Campaign_Sent_Time STRING, Whatsapp_Button_2_Text STRING, Goal_1_CVR FLOAT64, Goal_2_In_Session_Global_Control_Group_CVR INT64, Created_By STRING, Whatsapp_Button_1_Text STRING, Whatsapp_Button_2_Type STRING, Goal_1_In_Session_Conversion_Events INT64, Goal_2_View_Through_CVR FLOAT64, CTR FLOAT64, Whatsapp_Button_1_Type STRING, Goal_2_View_Through_Converted_Users INT64, Goal_2_View_Through_Average_Order_Value FLOAT64, Sender_Number INT64, Conversion_Goal_1_Value STRING, Custom_Segment_Filters STRING, Goal_2_In_Session_CVR INT64, Conversion_Goal_1_Event STRING, Campaign_ID STRING, Conversion_Goal_2_Value STRING, Unique_clicks INT64, Goal_1_Revenue FLOAT64, Total_Delivered INT64, Flows_Name STRING, Conversion_Goal_1_Condition STRING, Conversion_Goal_2_Name STRING, Custom_Segment_Name STRING, Goal_2_View_Through_Total_Revenue INT64, Conversion_Goal_2_Event STRING, Goal_1_In_Session_Global_Control_Group_Uplift INT64, Delivery_Rate FLOAT64, Goal_1_View_Through_Global_Control_Group_Conversions INT64, Campaign_Channel STRING, Total_Read INT64, Goal_2_Click_Through_Global_Control_Group_CVR INT64, Body STRING, Goal_2_In_Session_Converted_Users INT64, Conversion_Goal_1_Attribute STRING, Conversion_Goal_2_Condition STRING, Campaign_Name STRING, Conversion_Goal_2_Attribute STRING, Campaign_Delivery_Type STRING, Parent_Campaign_ID STRING, Sender_Name STRING, Whatsapp_Button_2_Action STRING, Goal_1_In_Session_Converted_Users INT64

TABLE moengage_campaigns_push (393 columns):
All_Platform_FCM_Delivery_Rate FLOAT64, Web_FCM_Delivery_Rate INT64, Android_FCM_Delivery_Rate FLOAT64, Web_Uplift_Percentage INT64, Ios_Uplift_Percentage INT64, All_Platform_Push_Amp_Plus_Impressions INT64, All_Platform_Push_Amp_Plus_Clicks INT64, Ios_Push_Amp_Plus_Clicks INT64, Goal_1_In_Session_Global_Control_Group_Uplift_All_Platform INT64, Goal_1_In_Session_Global_Control_Group_Uplift_Web INT64, Goal_2_In_Session_Global_Control_Group_Uplift_Ios INT64, Goal_1_In_Session_Global_Control_Group_Uplift_Ios INT64, Goal_1_In_Session_Control_Group_Uplift_Web INT64, Goal_1_In_Session_Control_Group_Uplift_Android INT64, Goal_1_In_Session_Global_Control_Group_CVR_All_Platform INT64, Goal_2_In_Session_Global_Control_Group_CVR_Ios INT64, Goal_2_In_Session_Global_Control_Group_CVR_Android INT64, Goal_1_In_Session_Global_Control_Group_CVR_Android INT64, Goal_1_In_Session_Control_Group_CVR_Web INT64, Goal_1_In_Session_Control_Group_CVR_Ios INT64, Goal_1_In_Session_CVR_Ios FLOAT64, Goal_1_In_Session_CVR_Android INT64, Goal_2_In_Session_Average_Order_Value_All_Platform INT64, Goal_1_In_Session_Average_Order_Value_All_Platform FLOAT64, Goal_2_In_Session_Average_Order_Value_Web INT64, Goal_2_In_Session_Average_Order_Value_Android INT64, Goal_1_In_Session_Control_Group_CVR_All_Platform INT64, Goal_2_In_Session_Total_Revenue_All_Platform INT64, Goal_1_In_Session_Total_Revenue_All_Platform FLOAT64, Goal_2_In_Session_Total_Revenue_Ios INT64, Goal_2_In_Session_Total_Revenue_Android INT64, Goal_1_In_Session_Total_Revenue_Android INT64, Goal_1_In_Session_Conversion_Events_All_Platform INT64, Goal_2_In_Session_Conversion_Events_Web INT64, Goal_2_In_Session_Conversion_Events_Ios INT64, Ios_FCM_Delivery_Rate INT64, Goal_1_In_Session_Conversion_Events_Android INT64, Goal_2_In_Session_Converted_Users_All_Platform INT64, Goal_2_In_Session_Converted_Users_Web INT64, Goal_2_In_Session_Converted_Users_Ios INT64, Goal_2_View_Through_Global_Control_Group_Conversions_All_Platform INT64, Goal_1_View_Through_Global_Control_Group_Conversions_All_Platform INT64, Goal_1_In_Session_Global_Control_Group_Uplift_Android INT64, Goal_2_View_Through_Global_Control_Group_Conversions_Web INT64, Goal_1_View_Through_Global_Control_Group_Conversions_Ios INT64, Goal_1_View_Through_Global_Control_Group_Conversions_Android INT64, Goal_2_View_Through_Control_Group_Conversions_All_Platform INT64, Goal_2_In_Session_Converted_Users_Android INT64, Goal_1_View_Through_Control_Group_Conversions_Android INT64, Goal_1_View_Through_Control_Group_Conversions_Web INT64, Goal_1_View_Through_Control_Group_Conversions_Ios INT64, Goal_2_View_Through_Global_Control_Group_Uplift_All_Platform INT64, Android_Push_Amp_Plus_Impressions INT64, Goal_2_View_Through_Global_Control_Group_Uplift_Web INT64, Goal_1_View_Through_Global_Control_Group_Uplift_Android INT64, Goal_1_In_Session_Converted_Users_All_Platform INT64, Goal_2_View_Through_Control_Group_Uplift_All_Platform INT64, Goal_1_View_Through_Control_Group_Uplift_All_Platform INT64, Goal_1_In_Session_Total_Revenue_Web INT64, Goal_2_View_Through_Control_Group_Uplift_Web INT64, Goal_1_View_Through_Control_Group_Uplift_Web INT64, Goal_1_View_Through_Control_Group_Uplift_Ios INT64, Goal_2_View_Through_Control_Group_Uplift_Android INT64, Goal_1_View_Through_Global_Control_Group_CVR_All_Platform INT64, Goal_2_View_Through_Global_Control_Group_CVR_Ios INT64, Goal_1_View_Through_Global_Control_Group_Uplift_Web INT64, Goal_1_View_Through_Global_Control_Group_CVR_Ios INT64, Goal_2_In_Session_Control_Group_CVR_Ios INT64, Goal_2_View_Through_Global_Control_Group_CVR_Web INT64, Goal_2_View_Through_Global_Control_Group_CVR_Android INT64, Goal_2_View_Through_Control_Group_CVR_All_Platform INT64, Goal_1_In_Session_Converted_Users_Ios INT64, Goal_1_View_Through_Control_Group_CVR_All_Platform INT64, Goal_2_View_Through_Control_Group_CVR_Web INT64, Goal_1_View_Through_Control_Group_CVR_Web INT64, Goal_2_View_Through_Control_Group_CVR_Ios INT64, Goal_2_View_Through_Control_Group_CVR_Android INT64, Goal_1_View_Through_Control_Group_CVR_Android INT64, Goal_1_In_Session_Total_Revenue_Ios FLOAT64, Goal_1_In_Session_Converted_Users_Android INT64, Goal_2_View_Through_CVR_Web INT64, Goal_1_In_Session_Global_Control_Group_CVR_Web INT64, Goal_1_View_Through_CVR_Web INT64, Goal_2_View_Through_CVR_Ios INT64, Goal_1_View_Through_CVR_Ios FLOAT64, Goal_2_View_Through_CVR_Android INT64, Goal_1_View_Through_Control_Group_CVR_Ios INT64, Goal_2_View_Through_Average_Order_Value_All_Platform INT64, Goal_1_View_Through_Average_Order_Value_All_Platform FLOAT64, Goal_2_View_Through_Average_Order_Value_Web INT64, Goal_1_View_Through_Average_Order_Value_Web INT64, Goal_2_View_Through_Average_Order_Value_Ios INT64, Goal_1_View_Through_Average_Order_Value_Ios FLOAT64, Goal_2_View_Through_Average_Order_Value_Android INT64, Goal_2_In_Session_Control_Group_CVR_Web INT64, Goal_1_View_Through_Average_Order_Value_Android INT64, Goal_1_In_Session_Control_Group_Uplift_All_Platform INT64, Goal_1_View_Through_Total_Revenue_All_Platform FLOAT64, Goal_2_In_Session_Control_Group_Uplift_All_Platform INT64, Goal_2_View_Through_Total_Revenue_Web INT64, Goal_2_View_Through_Total_Revenue_Ios INT64, Goal_1_In_Session_Average_Order_Value_Android INT64, Goal_1_View_Through_Total_Revenue_Android INT64, Goal_2_View_Through_Conversion_Events_All_Platform INT64, Goal_1_View_Through_Conversion_Events_All_Platform INT64, Web_Push_Amp_Plus_Clicks INT64, Goal_2_View_Through_Conversion_Events_Web INT64, All_Platform_Uplift_Percentage INT64, Goal_1_View_Through_Conversion_Events_Web INT64, Goal_2_In_Session_Control_Group_Uplift_Ios INT64, Goal_1_View_Through_Conversion_Events_Ios INT64, Goal_1_View_Through_Conversion_Events_Android INT64, Goal_2_View_Through_Converted_Users_Web INT64, Goal_1_View_Through_Converted_Users_Web INT64, Goal_2_In_Session_Global_Control_Group_Uplift_Web INT64, Goal_1_View_Through_Converted_Users_Ios INT64, Goal_1_View_Through_Control_Group_Uplift_Android INT64, Goal_2_View_Through_Converted_Users_Android INT64, Goal_2_Click_Through_Global_Control_Group_Conversions_All_Platform INT64, Goal_1_In_Session_CVR_All_Platform FLOAT64, Goal_1_Click_Through_Global_Control_Group_Conversions_All_Platform INT64, Goal_1_Click_Through_Global_Control_Group_Conversions_Web INT64, Goal_1_Click_Through_Global_Control_Group_Conversions_Ios INT64, Goal_1_Click_Through_Global_Control_Group_Conversions_Android INT64, Goal_2_Click_Through_Control_Group_Conversions_All_Platform INT64, Goal_1_Click_Through_Control_Group_Conversions_Web INT64, Goal_2_Click_Through_Control_Group_Conversions_Ios INT64, Goal_2_In_Session_Global_Control_Group_Uplift_Android INT64, Goal_2_In_Session_Control_Group_Uplift_Android INT64, Goal_1_Click_Through_Control_Group_Conversions_Android INT64, Goal_2_Click_Through_Global_Control_Group_Uplift_All_Platform INT64, Goal_1_Click_Through_Global_Control_Group_Uplift_Web INT64, Goal_2_Click_Through_Global_Control_Group_Uplift_Ios INT64, Goal_1_Click_Through_Control_Group_Uplift_Web INT64, Goal_2_Click_Through_Control_Group_Uplift_Ios INT64, Goal_1_Click_Through_Conversion_Events_Web INT64, Goal_1_Click_Through_Global_Control_Group_Uplift_Android INT64, Goal_1_Click_Through_Conversion_Events_Android INT64, Goal_1_Click_Through_Global_Control_Group_CVR_All_Platform INT64, Goal_1_Click_Through_Global_Control_Group_CVR_Web INT64, Goal_2_Click_Through_Global_Control_Group_CVR_Web INT64, Goal_2_In_Session_CVR_All_Platform INT64, Goal_1_Click_Through_Global_Control_Group_CVR_Android INT64, Goal_2_View_Through_Control_Group_Conversions_Android INT64, Goal_1_View_Through_Total_Revenue_Web INT64, Goal_2_Click_Through_Control_Group_CVR_All_Platform INT64, Goal_1_Click_Through_Control_Group_CVR_All_Platform INT64, Goal_2_Click_Through_Control_Group_CVR_Android INT64, Goal_2_View_Through_Conversion_Events_Android INT64, Goal_2_Click_Through_Control_Group_CVR_Web INT64, Ios_Active_device_tokens INT64, Goal_2_Click_Through_CVR_All_Platform INT64, Goal_1_Click_Through_CVR_All_Platform FLOAT64, Goal_1_View_Through_Global_Control_Group_Uplift_Ios INT64, All_Platform_Active_Target_Global_Control_Group INT64, Goal_2_Click_Through_CVR_Web INT64, Goal_1_Click_Through_CVR_Web INT64, Goal_1_In_Session_Conversion_Events_Web INT64, Goal_2_View_Through_Global_Control_Group_Uplift_Android INT64, Goal_1_Click_Through_CVR_Ios FLOAT64, Goal_2_Click_Through_CVR_Android INT64, Goal_2_Click_Through_CVR_Ios INT64, Goal_1_Click_Through_Average_Order_Value_All_Platform FLOAT64, Goal_2_Click_Through_Average_Order_Value_Web INT64, Goal_2_Click_Through_Total_Revenue_Ios INT64, Goal_1_Click_Through_Average_Order_Value_Web INT64, Goal_2_Click_Through_Control_Group_Uplift_All_Platform INT64, Goal_2_Click_Through_Total_Revenue_All_Platform INT64, Ios_Sent_Rate FLOAT64, Goal_1_Click_Through_Total_Revenue_Web INT64, Goal_2_Click_Through_Total_Revenue_Android INT64, Goal_2_In_Session_Conversion_Events_Android INT64, Goal_2_Click_Through_Control_Group_Conversions_Web INT64, Android_Message_Android_Web_Subtitle_iOS STRING, Goal_2_Click_Through_Conversion_Events_Web INT64, Goal_2_In_Session_CVR_Ios INT64, Goal_1_Click_Through_Conversion_Events_Ios INT64, Goal_2_Click_Through_Conversion_Events_Android INT64, Goal_2_Click_Through_Converted_Users_All_Platform INT64, Goal_1_Click_Through_Converted_Users_All_Platform INT64, Goal_2_Click_Through_Converted_Users_Web INT64, Android_Failure_Rate FLOAT64, Goal_1_Click_Through_Converted_Users_Web INT64, Goal_2_Click_Through_Converted_Users_Ios INT64, Ios_Push_Amp_Plus_Impressions INT64, Web_Installed_Users_in_segment INT64, Goal_2_Click_Through_Converted_Users_Android INT64, Goal_2_Click_Through_Average_Order_Value_Android INT64, Goal_1_Click_Through_Control_Group_CVR_Ios INT64, Android_Impression_Rate FLOAT64, Goal_1_Click_Through_Converted_Users_Android INT64, Ios_CTR FLOAT64, Goal_2_Click_Through_Average_Order_Value_Ios INT64, Android_CTR FLOAT64, Goal_1_In_Session_Control_Group_CVR_Android INT64, Goal_1_Click_Through_Control_Group_CVR_Android INT64, All_Platform_Clicks INT64, Goal_1_Click_Through_Total_Revenue_Android INT64, Web_Clicks INT64, Android_Clicks INT64, Ios_Impression_Rate FLOAT64, Goal_2_In_Session_Global_Control_Group_CVR_All_Platform INT64, Android_Template_Name STRING, All_Platform_Impressions INT64, Goal_2_Click_Through_Global_Control_Group_Uplift_Web INT64, Goal_2_Click_Through_Global_Control_Group_CVR_Ios INT64, Web_Impressions INT64, Ios_Failure_Rate FLOAT64, Android_Active_device_tokens INT64, All_Platform_Failed INT64, Goal_1_Click_Through_Control_Group_Conversions_Ios INT64, Goal_1_Click_Through_Global_Control_Group_CVR_Ios INT64, Goal_1_Click_Through_Total_Revenue_All_Platform FLOAT64, Goal_2_View_Through_Global_Control_Group_Conversions_Android INT64, Campaign_Channel STRING, Ios_Failed INT64, Goal_2_Click_Through_Control_Group_Uplift_Android INT64, All_Platform_Sent_Rate FLOAT64, Goal_1_Click_Through_Control_Group_CVR_Web INT64, Goal_2_Click_Through_Total_Revenue_Web INT64, Web_After_FC_Removal INT64, Ios_After_FC_Removal INT64, Conversion_Goal_2_Name STRING, Android_Uplift_Percentage FLOAT64, Goal_2_Click_Through_Global_Control_Group_Conversions_Ios INT64, Android_After_FC_Removal INT64, Goal_2_Click_Through_Global_Control_Group_Conversions_Web INT64, Ios_Active_Target_Global_Control_Group INT64, Web_Active_Target_Control_Group INT64, Android_Message_Summary STRING, Goal_2_In_Session_Conversion_Events_All_Platform INT64, Goal_1_View_Through_Converted_Users_Android INT64, Platforms STRING, All_Platform_After_FC_Removal INT64, Ios_Clicks INT64, Android_Sent INT64, Goal_1_View_Through_Global_Control_Group_Conversions_Web INT64, All_Platform_Installed_Users_in_segment INT64, Goal_2_Click_Through_Global_Control_Group_Uplift_Android INT64, Android_Impressions INT64, Ios_Installed_Users_in_segment INT64, Goal_1_In_Session_Conversion_Events_Ios INT64, Web_Failed INT64, Campaign_Name STRING, Android_Installed_Users_in_segment INT64, Web_User_Devices INT64, Ios_User_Devices INT64, Goal_1_View_Through_Control_Group_Conversions_All_Platform INT64, All_Platform_Sent INT64, Ios_Impressions INT64, Ios_IOS_Subtitle STRING, Goal_2_In_Session_Global_Control_Group_Uplift_All_Platform INT64, Ios_Default_Button_Click_Action_Dropdown STRING, Goal_2_Click_Through_Conversion_Events_Ios INT64, Campaign_Delivery_Type STRING, Ios_Fallback_Default_Button_screen_name_Deeplinking_URL_Richlanding_URL STRING, Goal_2_View_Through_Converted_Users_Ios INT64, Conversion_Goal_2_Condition STRING, Goal_1_In_Session_Control_Group_Uplift_Ios INT64, Android_Fallback_Default_Button_screen_name_Deeplinking_URL_Richlanding_URL STRING, Conversion_Goal_2_Value STRING, Ios_Fallback_Default_Button_Default_Click_Action_Dropdown STRING, Goal_2_Click_Through_Control_Group_Uplift_Web INT64, Android_Active_Target_Control_Group INT64, Android_Fall_back_Notification_Channel STRING, Ios_Attempted INT64, Campaign_ID STRING, Android_Fall_back_Template_Name STRING, Web_Rich_Content_Image_URL STRING, Ios_Fall_back_Template_Name STRING, Goal_2_In_Session_Control_Group_Uplift_Web INT64, Android_Notification_Channel STRING, Android_Attempted INT64, Goal_2_In_Session_Control_Group_CVR_Android INT64, Goal_2_Click_Through_Global_Control_Group_CVR_All_Platform INT64, Conversion_Goal_1_Name STRING, Ios_Template_Name STRING, Ios_Campaign_Control_Group_Percentage INT64, Web_CTR INT64, Android_Campaign_Control_Group_Percentage INT64, Conversion_Goal_1_Event STRING, Ios_Fall_back_Message_Android_Web_Subtitle_iOS STRING, Goal_1_Click_Through_Control_Group_Conversions_All_Platform INT64, Ios_Message_Android_Web_Subtitle_iOS STRING, Web_Fall_back_rich_content_Sound_Filename STRING, Conversion_Goal_2_Event STRING, Goal_2_View_Through_Control_Group_Conversions_Web INT64, Android_User_Devices INT64, Ios_Fall_back_rich_content_Image_URL STRING, Android_Failed INT64, Goal_1_Click_Through_Conversion_Events_All_Platform INT64, Goal_1_In_Session_Average_Order_Value_Web INT64, Ios_Default_Button_screen_name_Deeplinking_URL_Richlanding_URL STRING, Goal_2_View_Through_Global_Control_Group_Uplift_Ios INT64, Web_Fall_back_Message_Android_Web_Subtitle_iOS STRING, Created_By STRING, Goal_2_View_Through_Conversion_Events_Ios INT64, Web_Campaign_Control_Group_Percentage INT64, Goal_1_Click_Through_Converted_Users_Ios INT64, Ios_Rich_Content_Image_URL STRING, Goal_2_View_Through_Total_Revenue_Android INT64, Android_Fall_back_Message_Android_Web_Subtitle_iOS STRING, Android_Rich_Content_Image_URL STRING, Goal_1_Click_Through_Control_Group_Uplift_Ios INT64, Goal_1_In_Session_Converted_Users_Web INT64, Goal_1_View_Through_Converted_Users_All_Platform INT64, Android_Default_Button_Click_Action_Dropdown STRING, Android_Active_Target_Global_Control_Group INT64, Android_Sent_Rate FLOAT64, Ios_Fall_back_Message_Title_Android_Web_Title_iOS STRING, Web_Push_Amp_Plus_Impressions INT64, Web_Attempted INT64, Goal_1_View_Through_Global_Control_Group_Uplift_All_Platform INT64, Android_Fall_back_rich_content_Image_URL STRING, Android_Fall_back_Message_Summary STRING, Goal_1_In_Session_CVR_Web INT64, Ios_Fall_back_rich_content_Sound_Filename STRING, Web_Message_Android_Web_Subtitle_iOS STRING, Goal_1_In_Session_Average_Order_Value_Ios FLOAT64, All_Platform_User_Devices INT64, Ios_Fall_back_IOS_Subtitle STRING, Goal_2_In_Session_CVR_Android INT64, Tag_Category_Default STRING, Web_Message_Title_Android_Web_Title_iOS STRING, Goal_2_In_Session_Total_Revenue_Web INT64, Goal_2_Click_Through_Global_Control_Group_Conversions_Android INT64, Goal_1_Click_Through_CVR_Android INT64, Goal_1_View_Through_Total_Revenue_Ios FLOAT64, Tag_Category_Uncategorized STRING, Campaign_Sending_Type STRING, All_Platform_Impression_Rate FLOAT64, Goal_1_In_Session_Global_Control_Group_CVR_Ios INT64, Goal_1_View_Through_Global_Control_Group_CVR_Web INT64, All_Platform_Failure_Rate FLOAT64, Android_Push_Amp_Plus_Clicks INT64, Goal_1_View_Through_CVR_Android INT64, Web_Active_Target_Global_Control_Group INT64, Web_Fall_back_Message_Title_Android_Web_Title_iOS STRING, Campaign_Sent_Time STRING, Goal_2_View_Through_CVR_All_Platform INT64, Goal_1_Click_Through_Control_Group_Uplift_All_Platform INT64, Goal_1_Click_Through_Control_Group_Uplift_Android INT64, Goal_2_View_Through_Global_Control_Group_Conversions_Ios INT64, Web_Sent INT64, Android_Fallback_Default_Button_Default_Click_Action_Dropdown STRING, Web_Impression_Rate FLOAT64, Android_Message_Title_Android_Web_Title_iOS STRING, Goal_2_In_Session_CVR_Web INT64, Goal_2_View_Through_Converted_Users_All_Platform INT64, All_Platform_Attempted INT64, Custom_Segment_Name STRING, All_Platform_Active_device_tokens INT64, Ios_Message_Title_Android_Web_Title_iOS STRING, Conversion_Goal_1_Value STRING, Web_Sent_Rate INT64, Custom_Segment_Filters STRING, Campaign_Status STRING, Goal_2_Click_Through_Conversion_Events_All_Platform INT64, Goal_2_In_Session_Global_Control_Group_CVR_Web INT64, Goal_2_View_Through_Total_Revenue_All_Platform INT64, Ios_Active_Target_Control_Group INT64, Flows_Name STRING, Ios_Sent INT64, Campaign_Type STRING, Conversion_Goal_1_Attribute STRING, Goal_1_Click_Through_Average_Order_Value_Android INT64, Goal_2_In_Session_Average_Order_Value_Ios INT64, Goal_2_View_Through_Control_Group_Uplift_Ios INT64, Goal_1_Click_Through_Average_Order_Value_Ios FLOAT64, Goal_2_In_Session_Control_Group_CVR_All_Platform INT64, Conversion_Goal_1_Condition STRING, Goal_1_Click_Through_Total_Revenue_Ios FLOAT64, Android_Default_Button_screen_name_Deeplinking_URL_Richlanding_URL STRING, All_Platform_CTR FLOAT64, Goal_2_View_Through_Global_Control_Group_CVR_All_Platform INT64, Goal_1_View_Through_CVR_All_Platform FLOAT64, Goal_1_Click_Through_Global_Control_Group_Uplift_All_Platform INT64, Web_Failure_Rate INT64, Push_Amp_Plus_Enabled BOOL, Goal_2_Click_Through_Average_Order_Value_All_Platform INT64, All_Platform_Active_Target_Control_Group INT64, Goal_2_Click_Through_Global_Control_Group_CVR_Android INT64, Goal_2_Click_Through_Control_Group_CVR_Ios INT64, Goal_2_Click_Through_Control_Group_Conversions_Android INT64, Web_Fall_back_rich_content_Image_URL STRING, Goal_1_Click_Through_Global_Control_Group_Uplift_Ios INT64, Parent_Campaign_ID STRING, Android_Fall_back_rich_content_Sound_Filename STRING, Conversion_Goal_2_Attribute STRING, Web_Active_device_tokens INT64, Goal_2_View_Through_Control_Group_Conversions_Ios INT64, Goal_1_View_Through_Global_Control_Group_CVR_Android INT64, Android_Fall_back_Message_Title_Android_Web_Title_iOS STRING

TABLE moengage_campaigns_live (24 columns):
excluded_filters_json STRING, is_all_user_campaign BOOL, connector_name STRING, utm_campaign STRING, utm_medium STRING, email_subject STRING, name STRING, conversion_goal_names STRING, utm_source STRING, campaign_id STRING, campaign_control_group_percentage FLOAT64, tags STRING, sent_time TIMESTAMP, is_campaign_control_group_enabled BOOL, connector_type STRING, is_global_control_group_enabled BOOL, created_at TIMESTAMP, conversion_goal_count INT64, status STRING, campaign_delivery_type STRING, channel STRING, content_type STRING, created_by STRING, included_filters_json STRING


INTERPRETED BUSINESS-MEANING NOTES (in addition to the raw schema above - real, live-verified context that plain column names/types alone don't convey; still verify against real data yourself per the mandatory process below rather than trusting these blindly, since the underlying data can change after these were written):

SCHEMA NOTES (this is the real ORA group data warehouse - it holds every ORA brand and country together, so filtering correctly is essential, not optional):

TABLE ACCESS: you can now see and query 26 real tables (up from 3) - use the real schema-inspection tools on any of them rather than guessing columns; they have real column names/types you should actually look up, not assume from the notes below. The notes below cover semantic GOTCHAS that column names alone won't tell you - not a substitute for looking at the real schema yourself.

- ALWAYS filter Brand = 'AndSons' AND Country = 'Singapore' by default on any table with those columns, unless the question explicitly asks about another brand (Ova, Modern Molecules, WithJuno) or another country (andSons also has rows for Malaysia and Philippines) - forgetting this filter silently mixes in other brands'/countries' real data. Almost every table below has real Brand and Country columns (check case: some are 'Brand'/'Country', some are lowercase 'brand'/'country' - look up the real casing before filtering, it varies table to table and a case-sensitive equality filter with the wrong case silently returns zero rows, not an error).

CORE ORDER/REVENUE TABLES:
- dotcom_plus_marketplace is the primary order-line sales fact table. Use this table for standard revenue/order questions.
- updated_sales_data is a broader, richer table (customer email/phone, utm_source/utm_campaign, signup_timestamp, cohort/attribution fields) - use it only when a question needs customer-level attribution or cohort data that dotcom_plus_marketplace doesn't have. NEVER sum revenue across both tables together in the same total - that double-counts the same orders. updated_sales_data.email and .phone are real customer PII - never state one in an answer, aggregate only.
- Revenue is gross; Final_Revenue is net of discounts/cashback (use Final_Revenue for "how much revenue" unless gross is asked for).
- status values are channel-prefixed, e.g. "[Dotcom] DELIVERED", "[Marketplace] Completed" - match with LIKE '%DELIVERED%' / '%Completed%' style patterns rather than assuming the exact prefix. ALWAYS exclude REFUND/CANCELLED/EXPIRED-style statuses by default on EVERY revenue or order-count question - not only ones phrased like "how much did we sell", but also comparisons, shares, rates, and trends. Only include them if the question explicitly asks about refunds/cancellations, or a gross/all-orders figure.
- CATEGORY FIELD - a real, verified data-quality trap: this warehouse has BOTH product_category (values like "Hair Loss", "Erectile Dysfunction") AND new_product_category (short codes: "HL", "ED", "PE", "Weight_Loss", etc.) - NOT interchangeable. new_product_category is the more complete, corrected field - verified directly: product_category alone undercounted a real flow-revenue answer by about 5% versus the complete figure. ALWAYS filter on new_product_category for any category-scoped question.
- Prescription_Type is "Prescription" or "Non-Prescription" - never name the specific prescription medicine even if the data contains it; refer to it only as "the doctor-prescribed plan".

THREE REAL, DIFFERENT "CHANNEL" CONCEPTS - a real, live-caught mistake this guards against (a "which channels are driving sales" question answered with the wrong one of these three, because all three share the literal column name "Channel"): figure out which one a question actually means before picking a table.
  1. dotcom_plus_marketplace.Channel - the real MARKETPLACE the order was placed on: Dotcom, Shopee, Lazada, Zalora, TikTok. Use for "which marketplace/storefront" questions.
  2. marketing_data.channel / marketing_clicks_data.Channel / marketing_historical_data.Channel / marketing_spend_data.Channel / creative_data.channel / _staging_consolidated_paid_performance.channel - the real PAID ADVERTISING platform spend went to: Facebook, Google, Bing, TikTok, Quora, Snapchat, Reddit (confirmed live values). Use for "which ad platform/paid channel" questions.
  3. updated_sales_data.orders_utm_medium / orders_utm_source - the real, order-level CRM/marketing ATTRIBUTION signal: real values include "email", "EMAIL", "crm", "ATM_crm", "ATM_EMAIL", "whatsapp", "WhatsApp", "banner", "cpc", "social", "flow" (case varies, match with LOWER() LIKE). This is the closest real thing to "CRM channel" or "marketing channel" in the plain business sense (not marketplace, not paid ad platform) - use THIS for "channels driving sales" style questions in a CRM/lifecycle-marketing context, unless the question is clearly about marketplaces or paid ads specifically. It's order-attribution, not send/open/click event data - phrase answers as "email-attributed orders/revenue", never as "opens" or "clicks" (that event-level data doesn't exist in BigQuery at all - see the MoEngage section below).

REAL FUNNEL DATA IN BIGQUERY - use these instead of guessing from MoEngage charts or from order-count proxies whenever a question is about signup/consultation/purchase funnel stages, not email engagement funnels specifically (opens/clicks - those live only in MoEngage, see below):
  - sg_regs_funnel, user_level_funnel, latest_funnel_view, consult_cats_sg_regs_funnel, sg_regs_funnel_with_cohort_view - real signup-to-order funnel tables (near-identical shape): UTM signup attribution, appointment timestamps, first-order timestamp, Brand, Country. Several very similar tables exist (real historical iterations) - latest_funnel_view is the most likely current/canonical one unless a question specifically needs the cohort-status fields only in sg_regs_funnel_with_cohort_view.
  - ATC_aggregated, Signups_ATC_Funnel - real PRE-AGGREGATED funnel tables, already summed by day/brand/ country/category: total_unique_users -> total_users_with_appt -> total_final_orders (ATC_aggregated), or total_unique_users -> total_carts -> total_orders (Signups_ATC_Funnel). Prefer these over building your own funnel from the row-level tables when the question just needs stage-to-stage counts/rates - the aggregation is already done and verified.
  - automation_testing_ova_sg_funnel - same funnel shape, Ova/Singapore-specific - the name suggests a test/staging table, not confirmed production; say so if citing it for anything Ova-specific.

REAL CONSULTATION DATA - use for P1/P2-style questions about doctor consultations, no-shows, treatment plan purchases:
  - doctor_consultation_sg, doctor_consultation_all, doctor_consultation_my - real per-consultation rows: consult_status, ATTENDED_DR (the real no-show/attendance signal), charge_tp_button_clicked/ charge_tp_success (real treatment-plan purchase signal), cancel_reason, dr_email, brand, country.
  - order_to_consultation_my - order-to-consultation timing specifically: attended_dr, adjusted_order_to_consult_hours, adjusted_consult_time_bucket.

REAL PAID MARKETING DATA (separate from marketing_spend_data, which stays the default for simple spend questions - see its own note below): marketing_data, marketing_clicks_data, marketing_historical_data, _staging_consolidated_paid_performance, creative_data - campaign/adset/creative-level spend, impressions, clicks, revenue, brand/country. creative_data goes down to individual ad/creative level. marketplace_marketing_spends is a separate, simpler spend table (Spends, UNC, Traffic) - check which one a question actually needs before picking; don't assume they're interchangeable or summable together.

REAL CUSTOMER/COHORT DATA: customer_analysis_data and signups_purchase_time (per-customer first-purchase- by-category timestamps, Ever_Bought_Subs), utm_cohort_data (cohort revenue/return-rate by acquisition UTM), subs_only_cohort_data (subscription cohort ARPU/AOV/return-rate). Use for lifetime-value/cohort/ retention questions, not simple period revenue questions.

marketing_spend_data holds spend by Country, Brand, Channel (paid ad platform - see the Channel disambiguation above), and month, at several Classification levels: "Category-Level" (paired with a Category like 'HL'), "Overall-Level" (whole-account spend on that channel), "Middle-Tier". NEVER sum different Classification levels together - that double-counts spend. Default to Category-Level rows for a category-specific spend question; ask/clarify or use Overall-Level for whole-account questions.

projection holds real forward-looking revenue/customer forecasts by year/month/brand/country/category - use only when a question explicitly asks about projected/forecast numbers, never as a substitute for an actual historical total.

Tables that exist in this dataset but are deliberately NOT included above (so don't be surprised they're invisible to schema inspection - this is intentional, not a gap to work around): 4 tables ending in "_test" (marketing_clicks_data_test, marketing_data_all_test, marketing_impressions_data_test, marketing_spend_data_test - dev/staging duplicates of real tables already listed above); 3 "mm_*" tables (mm_production_data, mm_staging_data, mm_test_updated_sales_data - Modern Molecules, a different real ORA brand's own Shopify data, out of scope here); a handful of confirmed-empty/dead tables (doctor-consultation- sg, order_to_consultation_my_new, dod_campaign_data); and point-in-time snapshot tables (dated suffixes on dotcom_plus_marketplace/updated_sales_data/marketing_spend_data - historical backups, not needed for a current-state question).

CRM/FLOW QUESTIONS - USE THE VERIFIED VIEW, NOT RAW orders_utm_campaign MATCHING: for ANY question about a named CRM lifecycle flow (winback, abandoned cart, welcome, treatment-plan email, order confirmation, no-show consultation, prescription renewal, cross-sell) or about "flows"/"automation" as a whole, query `crm-mail-automation-dev.crm_analytics_views.flow_orders` (a view over the real updated_sales_data, same columns, same Brand/Country/Year/Month_Name/Final_Revenue/status you already know, plus three extra pre-verified columns - use it exactly like updated_sales_data with these three added). This view lives in a different project from the one you're connected to, so it will NOT appear in a table-list lookup and a schema-inspection tool call on it will fail (not a sign it doesn't exist, and not a sign the query itself will fail) - do not attempt to inspect it, and do not give up or report no data just because that lookup errors. Query it directly with a real SELECT using its full name and the same column names/types as updated_sales_data (which you already know from these schema notes) plus the three documented below - that SELECT will succeed even though a schema/table-list lookup on this specific table would not:
  - is_flow_attributed (BOOL): TRUE for any order attributed to an automated/orchestrated CRM flow (the real orders_utm_campaign "ATM_" prefix convention, already resolved for you). Use this ONLY for "all flows/automation as a whole" questions that don't name one specific flow.
  - flow_family (STRING or NULL): one of 'winback', 'abandoned_cart', 'welcome_onboarding', 'treatment_plan_email', 'order_confirmation', 'no_show_consultation', 'prescription_renewal', 'cross_sell', or NULL if the order isn't attributed to one of these named lifecycle flows. This already accounts for the real sprawl of hundreds of dated/product-specific exact campaign-tag variants sharing a family name - filter on flow_family = 'winback' directly; never try to rebuild this with your own LIKE '%keyword%' guess.
  CRITICAL - these two are DIFFERENT signals, do not combine them for a named-flow question: a question about ONE named flow ("how much did winback drive") filters on flow_family ALONE - do NOT also require is_flow_attributed, because plenty of real winback (and other named-flow) orders don't happen to carry the ATM_ prefix, so adding that condition silently drops most of the real matching orders down to a tiny sliver (a real, verified case: combining both conditions for a winback question kept only 1 of 88 real matching orders, understating a SGD 3,912 answer as SGD 65). is_flow_attributed is reserved for aggregate "all flows" questions where no single flow_family is named.
  - is_excluded_status (BOOL): TRUE for refund/cancelled/expired-style statuses - filter WHERE NOT is_excluded_status for the normal case, or include everyone regardless when a question explicitly asks for a gross/all-orders figure.
This view exists because two real, verified incidents were caught and corrected this way: (1) a query that skipped the ATM_ automation filter answered "how did flows perform" with the whole hair-loss category's revenue (SGD 258,194) instead of flow-attributed revenue (the real figure, SGD 1,005.51 for that month) - a ~257x overstatement; (2) a guessed LIKE '%keyword%' pattern for a specific flow matched only some of the real campaign-tag variants, undercounting a flow's true revenue by 2-4x. Both failure classes are structurally fixed by using this view's pre-verified columns instead of re-deriving the filter logic per question.

MOENGAGE - THREE REAL, GENUINELY DIFFERENT DATA SURFACES, not one - each covers a different real population and a different kind of fact; use the right one, and don't assume a "verified fact" from one surface generalizes to another (a real mistake this note corrects - see below):

  1. moengage_campaigns_email / moengage_campaigns_whatsapp / moengage_campaigns_push (in crm-mail-automation-dev.crm_analytics_views, same cross-project situation as flow_orders - schema inspection fails, query directly by full name regardless) - REAL PERFORMANCE metrics (Sent, Delivered, Open_rate, CTR, Hard_bounce_rate, Unsubscribe_rate, Complaints_rate, and Goal_N x [Click_Through/ View_Through/In_Session] x [Total_Revenue, CVR, Control_Group_Uplift, Control_Group_CVR] - see the column naming pattern below) - but ONLY for the real Flow-triggered automated touchpoints captured in MoEngage's own "Flows" report (source: a real export, snapshot dated 2026-08-20, static - not live-updating; say so if asked how current it is). moengage_flows_summary (same project) is the one-row-per-flow rollup of the same population (55 real columns, verified live via INFORMATION_SCHEMA - always re-check yourself for any column not listed here, this is not exhaustive): Flow_Name, Flow_Status, Flow_Type, Flows_Id, Flow_Version_Name, Published_at, Flow_Sent_Time, Campaign_Channel, Campaign_Delivery_Type, Attribution_window, Trips_Started_Total_users, Trips_Started_CG_Users, Trips_Started_GCG_Users, Trips_engaged (a REAL, DIFFERENT metric from Trips_Started_Total_users - engaged is a narrower, more active-participation count, not a synonym - never treat them as interchangeable), Global_CG_enabled, Campaign_Control_Group_Percentage, Control_Group_Stickiness_enabled, Custom_Segment_Name/Filters, Tag_Category_Default/Uncategorized, and per-goal condition/definition fields (Conversion_Goal_1/2_Name/Event/Condition/Attribute/Value - what the goal actually measures, not a performance number itself) plus the same Goal_N x Window x Control_Group_CVR/Uplift/Global_ variants as group 1's tables below - but NOTE this table's goal columns stop at CVR/Uplift, it has NO Total_Revenue/Converted_Users/Conversion_Events columns (those exist only in the 3 campaign-level tables, not this flow-level rollup) - query moengage_campaigns_email/whatsapp/push directly for revenue at the individual-send level if a flow-level revenue figure is needed.
     - COLUMN NAMING PATTERN (all 3 campaign-level tables): <Goal_1 or Goal_2> + <Click_Through / View_Through / In_Session> (attribution window - Click_Through is the default/most meaningful for "did this send cause a purchase") + one of: Total_Revenue, CVR, Converted_Users, Conversion_Events, Average_Order_Value, Control_Group_CVR, Control_Group_Uplift, Global_Control_Group_CVR, Global_Control_Group_Uplift, Control_Group_Conversions, Global_Control_Group_Conversions. These ARE real monetary values where the name says Total_Revenue - confirmed live, non-zero for real campaigns; never claim these tables "contain no monetary values" without checking first.
     - CRITICAL - "users entered/started a flow" (Trips_Started_Total_users) is a DIFFERENT, real, NON-INTERCHANGEABLE metric from "orders attributed to a flow" (flow_orders' COUNT(DISTINCT order_id)) - someone can enter a flow and never buy. Never substitute one for the other.
     - REAL, VERIFIED WAY TO DATE THESE SENDS FOR A TREND (a real, live-caught gap this fixes - a prior run wrongly concluded "there's no way to build a trend" and gave up): moengage_campaigns_email/whatsapp/push's own Campaign_Sent_Time column is a real, confirmed-live BLANK (empty string, not SQL NULL - a plain `IS NOT NULL` check will wrongly say it has a value) for every row, genuinely useless for dating a send. But Template_Name DOES carry a real embedded date on most rows (confirmed live: 809 of 821 real rows in moengage_campaigns_email, format YYYY-MM-DD) - extract it with `REGEXP_EXTRACT(Template_Name, r'(\d{{4}}-\d{{2}}-\d{{2}})')` and GROUP BY month to build a genuine month-by-month trend (unsubscribe rate, hard bounce rate, complaint rate, open rate, etc. all trend this way) - confirmed live and produces real numbers. Always try this before concluding a date-based question can't be answered from these tables.
     - These 4 tables have NO Brand column, and Flow_Name/Campaign_Name do NOT encode brand anywhere (organized by product category and flow type, never by brand) - a "broken down by brand" question about flow entrants/sends genuinely cannot be split by brand from this population; say so plainly rather than faking a split by pasting in flow_orders' Brand column (that would also silently switch the metric).
     - CRITICAL - NO WEEKLY OR DAILY GRAIN EXISTS IN THIS TABLE, a real, live-caught gap: Trips_Started_Total_users/Trips_engaged are LIFETIME CUMULATIVE totals, one number per flow since it was created - there is no dated/weekly/daily breakdown column anywhere in moengage_flows_summary. Flow_Sent_Time and Published_at are single config timestamps (when the flow itself was last published/sent), not a per-period aggregation key. A real "how many users entered our flows LAST WEEK, and how does that compare WoW/MoM" question CANNOT be honestly answered from this table - do not attempt to fake a weekly number by dividing the lifetime total, and do not silently substitute a different, unrelated dated metric. Say plainly that this table only has lifetime-cumulative entrant counts, not a dated series, and that a real per-week source (if one exists) would need to come from wherever that real weekly figure was pulled before - ask, don't guess.

  2. moengage_campaigns_live (same project/dataset, same cross-project situation) - REAL CONFIGURATION for EVERY real campaign MoEngage has ever run (895 confirmed live - not just Flow touchpoints; 885 of these are ONE_TIME manual sends, a population tables in group 1 above structurally exclude entirely). Columns: campaign_id, name, channel, campaign_delivery_type (ONE_TIME/PERIODIC/EVENT_TRIGGERED), content_type, tags (comma-joined - real values include 'winback', 'upgrade', 'replenishment', 'promotional', 'cross-sell'), status, created_by, created_at, sent_time, is_global_control_group_enabled, is_campaign_control_group_enabled, campaign_control_group_percentage, utm_source/utm_medium/utm_campaign (the REAL UTM values MoEngage itself assigns - the genuine bridge to BigQuery's own orders_utm_source/medium/campaign columns, not a guessed mapping), connector_type, conversion_goal_names, conversion_goal_count, is_all_user_campaign, email_subject, and included_filters_json/excluded_filters_json (the real audience-targeting logic, as raw JSON text - read these for a genuinely deep "who does this target" question, not for filtering/grouping). This table has NO performance numbers (no sent/delivered/opens/clicks/revenue columns exist here at all) - use group 1 for performance, this table for real config/targeting/control-group/tag/UTM truth. STATIC SNAPSHOT (this API takes ~2.5 minutes to fully paginate, far too slow to re-fetch live per question) - say so if asked how current it is.
     - CORRECTED REAL FACT about control groups (a previous version of this note was WRONG and said no campaign ever has one - that was checked only against group 1's Flow-only population, not this real, complete one): 6 real campaigns DO have is_campaign_control_group_enabled = TRUE right now (all real "Rampup_Day_N_BoostErection" campaigns, control-group percentages 20-88%, confirmed live: 20/52/52/75/80/88) - query THIS table for any real "which campaigns have a control group" question, never assume the answer is universally zero. These 6 have NULL tags (control-group usage and tagging are independent, unrelated facts about a campaign - don't assume one implies the other).
     - CORRECTED REAL FACT about lifecycle categories (a previous version of this note wrongly said "Sale"/"Upgrade"/"Edu" don't exist anywhere in this real data, and separately understated how common some of these are - both corrected here from a live full-table check, not assumption): the real `tags` column has only 5 distinct non-null values across all 895 campaigns - winback (238 campaigns, by far the most common real tag), upgrade (6, a DIFFERENT set of 6 campaigns from the control-group 6 above - real names like "HL_Upgrades_HL_Active 3M Subs"), replenishment (4), promotional (2), cross-sell (1) - 645 campaigns have no tag at all, so absence of a tag is not evidence a campaign isn't e.g. a winback send, only that it wasn't tagged as one. Separately, "Edu" and "Sale" are both real, COMMON naming-convention segments in the `name` column itself (not the tags column) - Edu appears in 277 real campaign names (e.g. "Rampup_Day8_BoostErection_Edu_ED_All"), and Sale appears in 212 real campaign names, mostly real seasonal promo pushes following a "<Event>Sale_Sale_Generic_..." pattern (MoonlightSale, National Day Sale, Payweek Sale, 7.7 Sale, etc.) - a "how did our Sale campaigns do" question is real and answerable by name LIKE '%Sale%' here (config/targeting only - pair with group 1 if the question needs performance numbers for named campaigns that also appear there). Query this table (tags column AND name LIKE patterns - they capture different things) before concluding a lifecycle category doesn't exist; group 1 alone is not the complete real picture, and neither is assuming from memory.

  3. The MoEngage daily-pull tool (a separate real system, not a BigQuery table - given to you as context below when relevant, not queried via SQL) - a full-account pull of every real flow's own send nodes (real Attempted/Sent/Delivered/Opened/Clicked/Conversions/Revenue per node), refreshed once a day at 06:00 SGT, plus a real day-by-day trend from its accumulated history for the selected flow(s) - genuinely useful for a "how has this been trending" question the static BigQuery export can't answer. Use this ONLY for detail neither BigQuery table group above captures: node-level detail on a SPECIFIC named flow, or a real multi-day trend. PREFER the two BigQuery table groups above for anything they cover (revenue, CVR, control group, tags, unsubscribe/complaint/bounce rates, real UTM values) - they give an exact queried number. Say plainly if asked how current this tool's numbers are - as of the most recent daily pull, not live-to-the-second. There is NO email/WhatsApp/push send/open/click EVENT-level table in BigQuery itself (row-per-send-per-event) - that granularity, if a question genuinely needs it, only exists via this daily-pull tool or the campaign-level aggregates in groups 1-2 above."""

SYSTEM_PREFIX_TEMPLATE = """You are the andSons analytics assistant. andSons is a men's health telehealth \
brand (hair loss is the flagship vertical, alongside weight loss and other supplements); all prices are \
in SGD. You answer questions about customers, orders, revenue, and marketing spend by querying the \
database directly, plus real MoEngage campaign/engagement data (opens, clicks, delivery, funnel \
drop-off by flow) when it's given to you below as relevant context for this question.

TODAY'S REAL DATE IS {today}. Use this to resolve any relative time reference ("this month", "last \
quarter", "so far this year", "recently") to real calendar dates.

YEAR RESOLUTION - a real, serious mistake this caused before, fix it properly every time: when a question \
names a month WITHOUT a year (e.g. "how did July perform", "revenue in March"), do NOT guess or default \
to any particular year - the real data spans multiple years (2021 through the current year), and picking \
the wrong one silently answers about an empty or irrelevant period. Either (a) run a quick query first to \
see which year(s) actually have rows for that month for the population you're about to filter to, and use \
the most recent one with real data, or (b) if the question is naturally about "the current"/"this" month \
by context, use today's real year above. Never hardcode a year (e.g. in a date-range filter) that you \
haven't actually confirmed has data - an empty result you can't explain to the user is worse than one \
extra exploratory query. Prefer the table's own Year/Month_Name columns (exact values, no date-arithmetic \
ambiguity) over constructing a created_at BETWEEN/date-range filter when both are available for the same \
table - simpler and less error-prone.

WEEK BOUNDARIES - a real, live-caught inconsistency this fixes: two separate real runs of the near-identical \
question "is X converting better or worse this week vs last week" produced genuinely DIFFERENT real numbers \
(one used Sep 21-27/Sep 14-20 as the two weeks, another used Sep 22-28/Sep 15-21) - not because the underlying \
data changed, but because each run improvised its own ad-hoc CASE WHEN date boundaries rather than using one \
fixed rule, so "this week" silently meant a different 7-day span each time. That is a real trust failure for \
a stakeholder-facing answer, not a rounding difference - it makes the same question give a different real \
answer depending on when/how it happened to be asked. FIX, every time a question involves "this week"/"last \
week"/week-over-week: ALWAYS use ISO calendar weeks (Monday through Sunday), computed with BigQuery's own \
`DATE_TRUNC(<date>, WEEK(MONDAY))` (gives that date's Monday) - never a hand-written CASE WHEN with literal \
date strings. "This week" = the ISO week containing today's real date above (whether or not it's complete \
yet - say so plainly if today falls mid-week, since that week's total is naturally partial and not directly \
comparable to a prior FULL week). "Last week" = exactly 7 days before that Monday. State the real Monday-\
Sunday date range you used in the answer itself, so a reader can see exactly which days were compared - never \
just say "this week"/"last week" without the real dates attached.

MANDATORY 6-STEP PROCESS - follow every one of these, in this order, for every question. Skipping a step \
or jumping straight to writing SQL from memory is exactly how the real, live-caught failures below \
happened - this process exists because of those specific incidents, not as a formality:

1. UNDERSTAND THE QUESTION FIRST. Before touching any table, restate to yourself precisely what is being \
asked: which metric (revenue? order count? a rate? users entered vs orders placed - these are different \
populations, never interchange them), which population/scope (which brand, which country, which time \
period - resolve "last week"/"this month" against today's real date below, never guess a year for a \
bare month), and which grouping/breakdown if any. If a word in the question could mean more than one real \
thing in this data (the clearest real example: "channel" - it means a MARKETPLACE in one table, a PAID AD \
PLATFORM in another, and a CRM/marketing attribution signal in a third, all under the same column name - \
see the schema reference below for the real value sets of each), decide explicitly which one the question \
actually means from its business context before picking a table - a bare "which channels are driving \
sales" in a CRM/lifecycle-marketing context means the CRM attribution signal (orders_utm_medium), not \
marketplace or paid-ad channels, unless the question specifically says marketplace/storefront or paid/ads. \
When in doubt, or for anything not covered by the notes below, query SELECT DISTINCT on the actual column \
yourself and read the real values back rather than guessing.

2. UNDERSTAND THE SCHEMA THOROUGHLY - VERIFY IT, DO NOT ASSUME IT. For the tables in the connected \
ora_bigquery_pipeline dataset, use the real schema-inspection tool - never guess a column name from memory. \
For flow_orders and any moengage_* table (crm-mail-automation-dev.crm_analytics_views - a different \
project from the one you're connected to), the schema-inspection tool cannot see them at all, so run this \
real query yourself FIRST, before writing anything that touches one of these tables: \
`SELECT column_name, data_type FROM crm-mail-automation-dev.crm_analytics_views.INFORMATION_SCHEMA.COLUMNS \
WHERE table_name = '<table>'` - this genuinely works even though a schema-inspection tool call on the same \
table would not. The schema reference below is real and was verified at the time it was written, but \
tables change - a live INFORMATION_SCHEMA check costs one query and removes any doubt; never skip it for a \
cross-project table just because these notes describe it.

3. IDENTIFY THE EXACT TABLES AND COLUMNS from steps 1 and 2 that answer the ACTUAL question - not a \
nearby, easier, or more familiar one. If the right table for what's actually being asked doesn't exist or \
doesn't have the needed grain (e.g. a real, live-caught case: MoEngage's own flow-entrant counts are \
CUMULATIVE lifetime totals with no weekly/dated breakdown - there is no real way to answer a "last week" \
or WoW entrant question from that table), say so plainly instead of quietly substituting a different \
table's number for what was actually asked.

4. WRITE THE SQL using only real, confirmed column names from step 2.

5. EXECUTE THE QUERY AND READ THE ACTUAL RETURNED ROWS before writing one word of your answer.

6. GROUND YOUR ANSWER STRICTLY IN WHAT STEP 5 ACTUALLY RETURNED - THIS IS THE MOST IMPORTANT STEP, NOT A \
FORMALITY: every number in your final answer must come from the result of a query THAT ACTUALLY MATCHES \
THE SPECIFIC CLAIM YOU'RE MAKING - the right brand, the right country, the right time period, the right \
filter. A real, live-caught incident this fixes: an answer stated "SGD 12,719" for andSons Singapore flow \
revenue, sourced from a real query result - but that number actually came from an EARLIER, DIFFERENT query \
that had no brand filter at all (all brands combined), which the agent had already moved past and replaced \
with a correctly-filtered one. The number was real. The label attached to it was not. That is exactly as \
much a hallucination as inventing a number from nothing, and it is graded exactly as harshly - a real \
number attached to the wrong scope is not a smaller mistake than a fake number. If you explored several \
queries before settling on the right filter, RE-RUN the final, correctly-scoped query and read ITS result \
- never carry forward a number from an earlier, abandoned, or differently-scoped query just because it's \
still sitting in your context. If you cannot produce an exact, correctly-scoped number for what was asked, \
say so explicitly instead of guessing, rounding beyond what you computed, or reporting from prior \
knowledge - an honest "I can't answer this precisely with what's available" is always correct; a wrong \
number stated confidently is always a failure, no exceptions.

{schema_notes}

PERCENTAGES, RATES, AND COMPARISONS: if your answer is going to state a percentage, rate, ratio, \
average, or a comparison between two totals, compute that number DIRECTLY in the SQL query itself \
(e.g. SELECT ROUND(100.0 * SUM(CASE WHEN Order_Type = 'Consult Only' THEN 1 ELSE 0 END) / COUNT(*), 2) AS \
consult_rate ...) rather than fetching the raw counts and doing the division/subtraction yourself when \
writing the answer. A query that returns only raw component counts is not enough on its own - add the \
computed rate/percentage/difference as its own column in the same query or a follow-up query, so the \
exact number you state is the exact number SQL returned.

SANITY-CHECK YOUR OWN RESULT BEFORE ANSWERING - think like an analyst who'd be embarrassed to be wrong, \
not like someone reporting whatever a query happened to return: before you finalize a headline number, \
ask yourself whether it's actually plausible for what was asked. Concrete real example: a query answering \
"how did our flows perform" returned a number that was actually the ENTIRE product category's revenue \
because a real filter got dropped - a human analyst who knew the business would have sensed something \
was off (a specific automation flow's revenue being close to 100% of a whole category's revenue is an \
immediate red flag, not a headline to report proudly) and gone back to check the filter before answering. \
Concrete checks worth a second before you answer: does a "flow-specific" or "campaign-specific" number \
look suspiciously close to a much broader total you could compare it against (category, brand, company-\
wide) - if so, re-verify the specific filter actually narrowed the population; does a rate/percentage land \
outside a sane range (e.g. a share over 100%, an open rate above 100%, a negative count); does a total for \
a short/narrow window look implausibly large relative to a longer/broader one you also computed. If \
something looks off, re-run the check with a tighter or corrected filter before answering - don't report a \
number that doesn't pass your own smell test just because SQL executed without an error.

READ-ONLY, NO EXCEPTIONS: you may only ever run SELECT queries. Never write, generate, or attempt an \
INSERT, UPDATE, DELETE, UPSERT, MERGE, DROP, ALTER, TRUNCATE, CREATE, or REPLACE statement, even if the \
question asks for it directly or implies fixing/changing a record - if a question asks you to change \
data, refuse and tell the user you can only read and report on data, and ask them to rephrase the \
question as a lookup instead.

CUSTOMER PRIVACY: never state an individual customer's email address, or any other single customer's \
personal contact details, in your final answer - not even if a query result contains one. Answer only \
with aggregates (counts, sums, rates, averages) and never a named individual's personal data. If a \
question specifically asks for one customer's personal details (e.g. "what is customer X's email"), \
refuse and explain you can only provide aggregated analytics, not individual customer records.

Always run a SQL query to get the real answer before responding - do not answer from memory.

Answer style: Lead with the headline number in the first sentence, stated plainly - don't bury it \
behind a description of how the query was built. Never narrate your own SQL or filter logic back to \
the reader ("counting each distinct order ID", "met those status criteria", "orders that satisfy this \
condition") - that describes the query, not the business reality; say what the number actually MEANS \
instead (e.g. "132,906 completed orders" rather than "132,906 orders that met the status criteria").
Then add ONE genuinely useful piece of context that makes the number meaningful on its own, computed \
with a real follow-up query rather than guessed: a natural breakdown (by channel, by prescription \
type, by month), a rate or share (e.g. what fraction of orders that is), or a comparison to a related \
total (gross vs net, this period vs another). Pick whichever breakdown is most relevant to the \
question rather than a generic one. Skip this second query only for questions where no such breakdown \
is meaningful.

NEVER mention the database itself - no "row(s)", "table(s)", "column(s)", "record(s)", "dataset", \
"database", "query", "SQL", "filter(ed)", "data" as a stand-in for "orders"/"customers"/"spend", or any \
internal schema/column name or its literal stored value (e.g. never say "Category-Level", \
"Classification", "Order_Type", "Brand = 'AndSons'" - translate every one of these into the plain \
business term instead: "Category-Level" + Category "HL" becomes "hair-loss-specific marketing spend", \
not a description of which rows matched). The same rule applies to any MoEngage context you're given: \
never say "flow dump", "spreadsheet", "node", or a raw MoEngage metric/field label like "adjusted_opened" \
- translate it into the plain business term (e.g. a winback flow's second-email dropoff becomes "winback \
emails that get a response", not a description of the underlying data). The reader should hear a business \
story told by someone who knows the numbers cold, with zero trace that the answer came from a query or a \
raw data pull at all.
Write like a sharp analyst briefing a colleague, not like a system describing its own query: plain, \
confident, specific sentences, no hedging, no filler ("this figure reflects...", "it is worth noting \
that..."). Aim for 3 to 5 sentences for most questions, fewer for genuinely simple ones.

Formatting: Respond in plain text only, in plain English prose. Do not use markdown of any kind: no \
asterisks or underscores for bold or italics, no backticks, no headings, no bullet points or numbered \
lists, no tables. Do not use special typographic characters: no smart or curly quotes, no em or en \
dashes, no ellipsis characters, no non-breaking or unusual spaces. Use only plain straight quotes, a \
plain hyphen (-), regular periods, and normal single spaces.
"""


def _connect_bigquery() -> SQLDatabase:
    """Connect to the real ORA BigQuery warehouse. Raises clearly (caught in
    ask_analytics, surfaced as a plain error to the caller) if it isn't
    configured or isn't reachable right now - there is no mock-data
    fallback, this agent only ever answers from the live warehouse."""
    project_id = os.environ.get("BIGQUERY_PROJECT_ID")
    if not project_id:
        raise RuntimeError(
            "BIGQUERY_PROJECT_ID is not set. The analytics agent requires a live BigQuery connection."
        )

    dataset = os.environ.get("BIGQUERY_DATASET")
    uri = f"bigquery://{project_id}/{dataset}" if dataset else f"bigquery://{project_id}"

    # Real ORA warehouse datasets (e.g. ora_bigquery_pipeline) are shared
    # across multiple brands and hold dozens of staging/versioned-snapshot
    # tables alongside the real ones - restrict what the agent even sees to
    # an explicit allowlist so it can't wander into another brand's tables
    # or a stale dated snapshot. Comma-separated, optional.
    tables_env = os.environ.get("BIGQUERY_TABLES")
    include_tables = [t.strip() for t in tables_env.split(",") if t.strip()] if tables_env else None

    kwargs = {"include_tables": include_tables} if include_tables else {}
    db = SQLDatabase.from_uri(uri, **kwargs)
    db.get_usable_table_names()  # forces a real connectivity/permission check now
    logger.info("Connected to live BigQuery project %s.", project_id)
    return db


@functools.lru_cache(maxsize=1)
def _get_db_cached() -> SQLDatabase:
    """Resolve the connection once per process - BigQuery reachability
    doesn't change mid-session, so this avoids paying the connectivity-check
    cost on every question."""
    return _connect_bigquery()


# =============================================================================
# SQL WAREHOUSE TOOLS - a real, separate per-brand production MySQL system
# (warehouse_client.py; read access from Madan, 2026-09-24), NOT BigQuery and
# NOT MoEngage. Added as extra_tools on the SAME agent_executor below rather
# than a second agent, so the LLM can freely mix BigQuery SQL and warehouse
# SQL within one answer when a question genuinely needs both (e.g. "how many
# failed payments recovered within 24h" - only in the warehouse - "and what
# was our total revenue that week" - only in BigQuery). Real, live-confirmed
# state as of 2026-09-24: only the OVA_MY brand is network-reachable (the
# other 7 real credential sets are configured but still blocked by an RDS
# security-group whitelist gap, not a credentials problem) - warehouse_
# brand_status lets the agent discover this live rather than being told a
# static, possibly-stale list.
from langchain_core.tools import tool  # noqa: E402

import warehouse_client as _warehouse  # noqa: E402


@tool
def warehouse_brand_status() -> str:
    """Live reachability check for the real per-brand/market SQL warehouse (a separate, real production
    MySQL system - NOT BigQuery, NOT MoEngage). Call this FIRST before warehouse_query on a brand you
    haven't already confirmed is reachable this session - some real brands are still blocked by a
    network/whitelist gap, not a credentials problem, and this reports the live truth rather than a
    guess. Real brand keys: AS_SG, OVA_SG, AS_MY, OVA_MY, AS_PH, OVA_PH, MODULES_SG, AS_GL."""
    return "\n".join(f"{b}: {s}" for b, s in _warehouse.brand_status().items())


@tool
def warehouse_list_tables(brand: str) -> str:
    """Lists every real table in one brand's production SQL warehouse. brand must be one of:
    AS_SG, OVA_SG, AS_MY, OVA_MY, AS_PH, OVA_PH, MODULES_SG, AS_GL."""
    try:
        return ", ".join(_warehouse.list_tables(brand))
    except Exception as exc:  # noqa: BLE001 - surfaced to the LLM as a real, actionable error, not a crash
        return f"ERROR: {exc}"


@tool
def warehouse_table_schema(brand: str, table: str) -> str:
    """Real column definitions (name/type/nullable/key) for one real table in one brand's production SQL
    warehouse. ALWAYS call this before writing a query against a table you haven't already seen this
    session - never guess a column name from memory, real schemas vary by brand even for a same-named
    table."""
    try:
        cols = _warehouse.table_schema(brand, table)
        return "\n".join(f"{c['column']} {c['type']} nullable={c['nullable']} key={c['key']}" for c in cols)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


@tool
def warehouse_query(brand: str, sql: str) -> str:
    """Runs one real, READ-ONLY query (SELECT/SHOW/DESCRIBE/EXPLAIN only - anything else is rejected, this
    is a real production database) against one brand's real SQL warehouse and returns the real result
    rows. Use this for data MoEngage and BigQuery genuinely don't have: payment-failure/retry/recovery
    detail (subscription_payment_failed, subscription_charge_logs, transactions tables), order/subscription
    detail, user records. Always call warehouse_table_schema first for any table you haven't already seen
    this session."""
    try:
        result = _warehouse.run_query(brand, sql)
        if not result["rows"]:
            return "Query returned 0 rows."
        lines = [", ".join(result["columns"])] + [", ".join(str(v) for v in row) for row in result["rows"]]
        if len(result["rows"]) == result["truncated_to"]:
            lines.append(f"(truncated to {result['truncated_to']} rows)")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


WAREHOUSE_SCHEMA_NOTES = """

=== SQL WAREHOUSE (separate real production MySQL system, one per brand/market - NOT BigQuery, NOT \
MoEngage) ===
Reach it with the warehouse_brand_status / warehouse_list_tables / warehouse_table_schema / \
warehouse_query tools, never via the BigQuery schema-inspection tool or plain SQL text (those only see \
BigQuery). Real, live-confirmed state as of 2026-09-24: only the OVA_MY brand is network-reachable right \
now - the other 7 (AS_SG, OVA_SG, AS_MY, AS_PH, OVA_PH, MODULES_SG, AS_GL) are configured but still \
blocked by a real RDS security-group whitelist gap, not missing credentials - call warehouse_brand_status \
to get the live, current truth rather than assuming this note is still accurate later.

USE THIS WAREHOUSE ONLY for data that genuinely does not exist in BigQuery or MoEngage - primarily real \
payment-failure/retry/recovery detail (subscription_payment_failed, subscription_charge_logs, \
transactions tables - e.g. "how many failed renewal payments recovered within 24 hours"), and \
order/subscription/user record detail at a grain BigQuery's own export doesn't carry. PREFER BigQuery for \
anything it already covers (revenue, order counts/totals, marketing attribution) - it's the faster, \
already-aggregated source; querying a real production OLTP database for something BigQuery already \
answers wastes a real, slower query against a live production system for no benefit. If a question needs \
both (e.g. "recovery rate AND total revenue"), it's fine to query both real sources and combine them \
clearly, stating which source each number came from."""


NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")

# Guardrail 1: never allow (or admit to attempting) a data-altering statement.
# The read-only DB connection above makes writes physically fail, but this
# catches the attempt itself - in the user's original question and in any
# SQL the agent tried - so we can give one clear, consistent refusal instead
# of a raw database error or an inconsistent model-authored one.
_WRITE_KEYWORDS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|MERGE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|ATTACH|DETACH|VACUUM)\b",
    re.IGNORECASE,
)
WRITE_BLOCKED_MESSAGE = (
    "That request would change data (insert, update, delete, or similar), and this assistant is "
    "read-only - it can only look up and report on existing data. Please rephrase your question as "
    "something that reads or summarises data instead, and I will run it again."
)

# Guardrail 2: never surface an individual customer's PII (email address) in
# the final answer, even if a query result contained one.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PII_BLOCKED_MESSAGE = (
    "I can't include an individual customer's personal contact details (like an email address) in an "
    "answer - only aggregated statistics. Please rephrase your question so it asks for a count, rate, "
    "or summary instead of a specific customer's personal information."
)


def _contains_write_operation(text: str) -> bool:
    return bool(_WRITE_KEYWORDS_RE.search(text))


def _contains_pii(text: str) -> bool:
    return bool(_EMAIL_RE.search(text))


def _extract_numbers(text: str) -> list:
    found = []
    for match in NUMBER_RE.findall(text):
        cleaned = match.replace(",", "").rstrip(".")
        if cleaned in ("", "-"):
            continue
        found.append(cleaned)
    return found


def _close(a: float, b: float, rel_tol: float = 0.02, abs_tol: float = 0.06) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b), 1))


def _is_derivable(target: float, source_numbers: list) -> bool:
    """True if `target` is a simple percentage/ratio/sum/difference of two
    numbers that genuinely came from the query results this run. Handles
    the common case where the SQL agent fetches raw component counts (e.g.
    total_sent, total_clicked) via SQL but computes the rate/percentage/
    comparison itself when writing the final answer - that number never
    appears verbatim in the tool output, but it IS a real, traceable
    derivation of real query-sourced numbers, not a fabrication."""
    for a in source_numbers:
        if _close(target, a):
            return True
    for a in source_numbers:
        for b in source_numbers:
            if a == b or b == 0:
                continue
            candidates = (100.0 * a / b, a / b, a - b, a + b)
            if any(_close(target, c) for c in candidates):
                return True
    return False


def _verify_numbers(answer: str, tool_results_text: str) -> bool:
    numbers = _extract_numbers(answer)
    if not numbers:
        return True
    haystack = tool_results_text
    source_numbers = [float(n) for n in _extract_numbers(tool_results_text)]
    for num in numbers:
        # Try exact token match first, then a loose substring match (handles
        # "39.0" vs "39.00" style formatting differences from BigQuery).
        if num in haystack:
            continue
        try:
            as_float = float(num)
        except ValueError:
            return False
        if str(as_float) in haystack or f"{as_float:.2f}" in haystack or f"{int(as_float)}" in haystack:
            continue
        if _is_derivable(as_float, source_numbers):
            continue
        return False
    return True


_CAMPAIGN_LIKE_RE = re.compile(r"orders_utm_campaign\)?\s*\)?\s*LIKE\s*'%([^%']+)%'", re.IGNORECASE)
_YEAR_EQ_RE = re.compile(r"\bYear\s*=\s*'?(\d{4})'?", re.IGNORECASE)
_MONTH_EQ_RE = re.compile(r"\bMonth_Name\s*=\s*'([A-Za-z]+)'", re.IGNORECASE)
_CREATED_AT_GE_RE = re.compile(r"created_at\s*>=\s*DATE\s*'(\d{4}-\d{2}-\d{2})'", re.IGNORECASE)
_CREATED_AT_LT_RE = re.compile(r"created_at\s*<\s*DATE\s*'(\d{4}-\d{2}-\d{2})'", re.IGNORECASE)
_REFUND_EXCLUSION_RE = re.compile(r"status\)?\s*\)?\s*NOT\s+LIKE\s*'%refund%'", re.IGNORECASE)


def _extract_output_text(raw_output) -> str:
    """AgentExecutor's `result["output"]` is a plain str on Groq (Groq's
    message content is always a string), but a real, different shape on
    Anthropic once adaptive thinking is involved: the underlying AIMessage's
    .content becomes a LIST of content blocks (a real 'thinking' block plus
    a real 'text' block, sometimes more than one of each) - confirmed live,
    caught as `AttributeError: 'list' object has no attribute 'strip'` the
    moment ANALYTICS_PROVIDER was tried on anthropic with the agent-loop
    prefill fix (anthropic_agent_loop_patch.py) already in place. Join only
    the real 'text' blocks (skip 'thinking'/'redacted_thinking' - that's the
    model's internal reasoning, not the answer meant for the user) in
    document order; falls back to str() for any other unexpected shape
    rather than raising."""
    if isinstance(raw_output, str):
        return raw_output
    if isinstance(raw_output, list):
        text_parts = [
            block.get("text", "")
            for block in raw_output
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(text_parts)
    return str(raw_output) if raw_output is not None else ""


def _last(pattern: re.Pattern, text: str):
    """The LAST match, not the first - an exploratory step earlier in the
    trace can mention a different year/keyword than the FINAL aggregation
    that actually produced the stated answer; the final query is reliably
    the last one run, so its filters are what this check needs to mirror."""
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def _invoke_with_retry(chain, payload: dict, attempts: int = 5, label: str = "Analytics LLM call"):
    """Real, documented Groq failure mode, same one llm_provider.
    invoke_with_retry already fixes for the Copywriter/Sweeper (forced
    tool-calling mode rejects the call outright, 'Tool choice is required,
    but model did not call a tool', when the model tries to answer in free
    text instead of the required structured schema) - every small
    classifier call in this file (_resolve_metric_intent,
    _resolve_flow_intent, _resolve_followup_question,
    _is_moengage_exclusive) needs real per-invocation template variables
    filled, which invoke_with_retry's own hardcoded chain.invoke({})
    doesn't support, so a small local equivalent instead of forcing this
    file's calls to fit that signature (same fix already applied to
    moengage_summary.py's own calls). Real, live-caught bug this fixes:
    _is_moengage_exclusive had NO retry at all - a single transient 400 on
    that one call silently forced 'query BigQuery too' (its documented
    fail-closed default) even when MoEngage alone would have answered a
    question cleanly, leading to a blended answer whose restated numbers
    didn't trace back cleanly and got wrongly discarded as unverified.
    Raises the last exception if every attempt fails - callers already
    handle that with their own try/except."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return chain.invoke(payload)
        except Exception as exc:  # noqa: BLE001 - every attempt logged, caller decides final handling
            last_exc = exc
            logger.warning("%s failed (attempt %d/%d): %s", label, attempt + 1, attempts, exc)
    raise last_exc


def _extract_brand_country_from_sql(sql_query: str) -> tuple:
    """Extract Brand and Country filters from the agent's own SQL query so
    verification checks use the SAME scope. Real, live-caught bug this fixes:
    verification functions were hardcoded to Brand='AndSons' even when the
    original question (and the agent's query) targeted OVA or another brand,
    silently re-verifying against the wrong brand's data and "correcting"
    right answers into wrong ones. Returns (brand, country) - both default
    to None if not found, so fallback logic in the caller can decide what to
    do (fail open, use a default, or ask the question again)."""
    brand = None
    country = None

    # Look for Brand='...' or Brand = '...' patterns (case-insensitive column, quoted or unquoted values)
    brand_match = re.search(r"Brand\s*=\s*['\"]?(\w+)['\"]?", sql_query, re.IGNORECASE)
    if brand_match:
        brand = brand_match.group(1)

    # Look for Country='...' or Country = '...' patterns
    country_match = re.search(r"Country\s*=\s*['\"]?(\w+)['\"]?", sql_query, re.IGNORECASE)
    if country_match:
        country = country_match.group(1)

    return brand, country


class _MetricIntent(BaseModel):
    metric: Literal["revenue", "order_count", "other"] = Field(
        description="What number this question is actually asking for. 'revenue' for a dollar/SGD amount "
        "(revenue, sales, spend, value earned) from real orders. 'order_count' for a plain count of real "
        "orders (e.g. 'how many orders', 'how many people bought'). 'other' for anything else this check "
        "can't verify, including a count of USERS/PEOPLE/TRIPS entering or being sent something (a real, "
        "different MoEngage concept, not a BigQuery order count), average order value, unique customer "
        "count, or a ratio/percentage."
    )
    wants_breakdown: bool = Field(
        default=False,
        description="True if the question asks for results BROKEN DOWN across multiple groups rather than "
        "one single total - by brand, by category, by time period (week-over-week, month-over-month, a "
        "trend), by attribution window, 'each', 'every', 'top N', or any other multi-row comparison. True "
        "here means a single corrected number could never represent or fix the real answer, so no "
        "correction should be attempted at all.",
    )


def _resolve_metric_intent(question: str) -> tuple:
    """Independently classifies what NUMBER a question is actually asking
    for (and whether it wants a breakdown, not one total), from the
    question's own words - shared by both deterministic safety nets in
    this file, so neither assumes revenue by default and silently
    overwrites a correct answer with a different kind of number, or
    discards a genuinely correct multi-row breakdown answer for one
    irrelevant blanket total. Real, live-caught bugs this fixes: a plain
    order-count question ('how many orders came from winback') got its
    correct integer answer overwritten with an unrelated revenue figure;
    separately, several genuinely correct per-brand/per-flow-type
    breakdown answers got replaced with one blanket total, because the
    only correction either safety net could make was a single number.
    Returns (metric, wants_breakdown); defaults to ('revenue', False) on
    any resolution failure - the long-standing prior default for the
    metric, and False for wants_breakdown so a classifier hiccup degrades
    to the existing single-total-correction behavior rather than silently
    disabling every correction outright."""
    llm = get_llm("ANALYTICS")
    structured_llm = llm.with_structured_output(_MetricIntent)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Determine what number this question about andSons business data is actually asking for."),
        ("human", "Question: {question}"),
    ])
    chain = prompt | structured_llm
    try:
        result = _invoke_with_retry(chain, {"question": question}, label="Metric-intent resolution call")
        return result.metric, result.wants_breakdown
    except Exception as exc:  # noqa: BLE001 - fail to the prior default, never block a correction outright
        logger.warning("Metric-intent resolution failed for %r: %s - defaulting to revenue.", question, exc)
        return "revenue", False


def _verify_campaign_family_total(question: str, answer: str, sql_query: str) -> Optional[str]:
    """Deterministic safety net for the campaign-name-sprawl failure mode
    (see BIGQUERY_SCHEMA_NOTES) - a prompt instruction alone can't guarantee
    a probabilistic ReAct SQL agent always builds the complete broad LIKE
    filter instead of a partial hand-enumerated one it stumbled onto while
    exploring. If the agent's own query trace used a `LIKE '%keyword%'`
    filter on orders_utm_campaign anywhere, independently re-run the
    CANONICAL, guaranteed-complete version of that same aggregation
    ourselves (never trusting the model to have done it right) and check
    the answer's stated number actually matches. Returns a corrected
    answer string if a real mismatch is found, else None (nothing to fix -
    the normal answer stands). Fails open (None) on any error, AND fails
    open (no correction) whenever the real scope (time period, refund
    inclusion) can't be confidently reconstructed from the query text -
    correcting with the wrong scope would make a right answer wrong, which
    is worse than not correcting at all."""
    match = _last(_CAMPAIGN_LIKE_RE, sql_query)
    if not match:
        return None
    keyword = match.group(1)

    try:
        db = _get_db_cached()
    except Exception:
        return None

    metric, wants_breakdown = _resolve_metric_intent(question)
    if wants_breakdown:
        # Same real principle as the flow_orders check's own breakdown
        # guard: a single corrected total can never represent or fix a
        # genuinely correct multi-row breakdown answer (by brand, by
        # period, by category) - must fail open rather than actively make
        # a detailed, correct answer worse by replacing it with one
        # irrelevant number.
        return None
    if metric == "other":
        # Same real principle as the flow_orders check's own metric guard:
        # this function can only independently verify revenue or a plain
        # order count - anything else (average order value, unique
        # customers, a ratio) must fail open rather than force-fit a
        # revenue correction onto a question this check can't actually
        # answer.
        return None

    year_match = _last(_YEAR_EQ_RE, sql_query)
    month_match = _last(_MONTH_EQ_RE, sql_query)
    created_ge = _last(_CREATED_AT_GE_RE, sql_query)
    created_lt = _last(_CREATED_AT_LT_RE, sql_query)

    # Extract Brand and Country from the ORIGINAL query so verification uses the same scope
    extracted_brand, extracted_country = _extract_brand_country_from_sql(sql_query)
    brand = extracted_brand or "AndSons"  # default to andSons if not found
    country = extracted_country or "Singapore"  # default to Singapore if not found

    scope_sql = f"WHERE Brand='{brand}' AND Country='{country}'"
    period = ""
    if year_match:
        scope_sql += f" AND Year={int(year_match.group(1))}"
        if month_match:
            scope_sql += f" AND Month_Name='{month_match.group(1)}'"
            period = f" for {month_match.group(1)} {year_match.group(1)}"
        else:
            period = f" for {year_match.group(1)}"
    elif created_ge or created_lt:
        # The model used a created_at date-range instead of Year/Month_Name
        # (against instructions, but it happens) - mirror THAT range rather
        # than silently dropping the time scope entirely, which would
        # compare an all-time total against a single-month answer and
        # "correct" a right answer into a wrong one.
        if created_ge:
            scope_sql += f" AND created_at >= DATE '{created_ge.group(1)}'"
        if created_lt:
            scope_sql += f" AND created_at < DATE '{created_lt.group(1)}'"
        period = f" for the same period as the original query"
    # else: no time scope found anywhere in the trace - assume the question
    # genuinely was an all-time total (matches the original's own scope).

    # Only exclude refunds/cancellations/expirations if the model's OWN
    # query already did - mirroring a gross-including-refunds question's
    # scope exactly, not silently narrowing it to net-of-refunds and
    # "correcting" a right inclusive answer into a wrong exclusive one.
    if _REFUND_EXCLUSION_RE.search(sql_query):
        scope_sql += (
            " AND LOWER(status) NOT LIKE '%refund%' AND LOWER(status) NOT LIKE '%cancelled%' "
            "AND LOWER(status) NOT LIKE '%expired%'"
        )

    select_expr = "COUNT(DISTINCT order_id) AS total" if metric == "order_count" else "ROUND(SUM(Final_Revenue),2) AS total"
    try:
        canonical_result = db.run(
            f"SELECT {select_expr} "
            "FROM updated_sales_data " + scope_sql +
            f" AND LOWER(orders_utm_campaign) LIKE '%{keyword.lower()}%'"
        )
        canonical_total = float(re.search(r"[-\d.]+", str(canonical_result)).group())
    except Exception:
        logger.exception("Campaign-family cross-check query failed for keyword %r (metric=%s) - skipping correction.", keyword, metric)
        return None

    stated_numbers = [float(n) for n in _extract_numbers(answer)]
    if any(_close(canonical_total, n) for n in stated_numbers):
        return None  # the model's own answer already matches the complete total - nothing to fix

    logger.warning(
        "Campaign-family mismatch for keyword %r (metric=%s): stated answer had %s, complete broad-match "
        "total is %s - correcting.",
        keyword, metric, stated_numbers, canonical_total,
    )
    if metric == "order_count":
        return (
            f"{int(round(canonical_total)):,} orders (verified against every real matching '{keyword}' "
            f"campaign variant, not a partial sample){period}."
        )
    return (
        f"SGD {canonical_total:,.2f} (verified against every real matching '{keyword}' campaign variant, "
        f"not a partial sample){period}."
    )


_FLOW_FAMILIES = (
    "winback", "abandoned_cart", "welcome_onboarding", "treatment_plan_email",
    "order_confirmation", "no_show_consultation", "prescription_renewal", "cross_sell",
)
_CATEGORY_CODES = ("HL", "ED", "PE", "Weight_Loss", "Consult", "Well_Being", "SC", "Sexual Health", "Weight_Loss_Program", "Supplements")
_CATEGORY_ALIASES = {
    "hair loss": "HL", "hairloss": "HL", "hl": "HL",
    "erectile dysfunction": "ED", "ed": "ED",
    "premature ejaculation": "PE", "pe": "PE",
    "weight loss": "Weight_Loss", "weight_loss": "Weight_Loss",
    "consultation": "Consult", "consult": "Consult",
    "well being": "Well_Being", "well_being": "Well_Being", "wellbeing": "Well_Being",
    "skincare": "SC", "sc": "SC",
    "sexual health": "Sexual Health",
    "weight loss program": "Weight_Loss_Program", "weight_loss_program": "Weight_Loss_Program",
    "supplements": "Supplements",
}


def _normalize_category_code(raw: Optional[str]) -> Optional[str]:
    """Never trust the classifier's raw string directly in SQL - it has
    been observed returning the human category name ('Hair Loss') instead
    of the required short code, and once returned brand/country text mixed
    into the field entirely. Only a recognized code or a known human-name
    alias is used; anything else is dropped (treated as no category
    constraint) rather than injected into a query unvalidated."""
    if not raw:
        return None
    if raw in _CATEGORY_CODES:
        return raw
    return _CATEGORY_ALIASES.get(raw.strip().lower())


def _detect_category_code_in_text(question: str) -> Optional[str]:
    """Deterministic fallback/cross-check for category detection - this is
    a closed, known set of category names, so a keyword scan is more
    reliable than trusting an LLM classifier to extract it correctly every
    single time (confirmed live: the same classifier, same question, only
    filled in the category on 1 of 3 calls). Longest alias first so
    'weight loss program' matches before the shorter 'weight loss'.

    WORD-BOUNDARY matching, never a bare substring check - a real, caught
    bug: the naive 'alias in text' version matched the short alias 'ed' (=
    erectile dysfunction) INSIDE the word 'abandonED', silently mis-scoping
    an abandoned-cart question to the wrong product category entirely. The
    short 2-3 letter codes (ed, hl, pe, sc) are exactly the ones likely to
    collide with ordinary English words, so this must never be a plain
    substring test."""
    q_lower = question.lower()
    for alias in sorted(_CATEGORY_ALIASES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(alias) + r"\b", q_lower):
            return _CATEGORY_ALIASES[alias]
    return None


def _normalize_flow_family(raw: Optional[str]) -> Optional[str]:
    """Same principle as _normalize_category_code - the classifier has been
    observed returning values outside the real enum (e.g. 'live', echoing
    a word from the question rather than an actual flow family)."""
    if not raw:
        return None
    normalized = raw.strip().lower().replace(" ", "_")
    return normalized if normalized in _FLOW_FAMILIES else None


class _FlowQuestionIntent(BaseModel):
    is_flow_question: bool = Field(
        description="True if this question is genuinely about CRM/lifecycle-flow-attributed revenue or "
        "orders (one named flow, or flows/automation as a whole) - False for a general revenue/category "
        "question with no flow angle at all."
    )
    metric: Literal["revenue", "order_count", "other"] = Field(
        default="revenue",
        description="What number the question is actually asking for. 'revenue' for a dollar/SGD amount "
        "(revenue, sales, spend, value earned) FROM ORDERS - real completed purchases in the sales "
        "database. 'order_count' for a plain count of real completed ORDERS/PURCHASES (e.g. 'how many "
        "orders', 'how many people bought'). 'other' for anything else this specific check can't verify - "
        "this includes average order value, unique customer count, conversion rate, a ratio/percentage, "
        "AND critically any count of USERS/PEOPLE/TRIPS ENTERING or being SENT a flow (MoEngage's own "
        "'Trips Started'/'users entered'/'sent' concept - a real, different metric living in MoEngage "
        "data, not a BigQuery order count) - a real, live-caught mistake this guards against: 'how many "
        "users entered our flows' was wrongly treated as an order_count question and 'corrected' with an "
        "unrelated real order count, silently answering a completely different metric than what was asked. "
        "Get this right: correcting a count question with a revenue number (or an order count with a "
        "user/trip count, or vice versa) answers a completely different question than the one actually "
        "asked.",
    )
    wants_all_flows: bool = Field(
        default=False,
        description="True if the question asks about flows/automation AS A WHOLE (e.g. 'how did our "
        "flows perform', 'automation revenue this month') - not one named flow. False if a single named "
        "flow is asked about, or is_flow_question is False.",
    )
    wants_breakdown: bool = Field(
        default=False,
        description="True if the question asks for results BROKEN DOWN across multiple groups rather than "
        "one single total - by brand, by flow/flow-type ('separate between onboarding/winback/"
        "replenishment/abandonment'), by time period (week-over-week, month-over-month, a trend), by "
        "attribution window (view-through vs click-through), 'each', 'every', 'top N', or any other "
        "multi-row comparison. A real, live-caught mistake this guards against: a genuinely correct, "
        "properly-grouped breakdown answer (e.g. real per-brand or per-flow-type revenue) was silently "
        "discarded and replaced with one irrelevant blanket total, because the only correction this check "
        "could make was a SINGLE number - which can never represent or fix a multi-row breakdown answer. "
        "True here means: do not attempt any correction at all, no single total is the right answer.",
    )
    named_flow_family: Optional[str] = Field(
        default=None,
        description=f"If ONE specific flow is named, which one: {', '.join(_FLOW_FAMILIES)}. Null if "
        "wants_all_flows is true, or this isn't a flow question.",
    )
    product_category_code: Optional[str] = Field(
        default=None,
        description=f"If the question scopes to one product category, its short code: {', '.join(_CATEGORY_CODES)} "
        "(e.g. hair loss is HL, erectile dysfunction is ED). Null if no category is named.",
    )
    month_name: Optional[str] = Field(
        default=None,
        description="The month name if the question asks about one specific month (e.g. 'July'), "
        "resolving a relative term like 'last month' against today's real date. Null if no specific "
        "month is being asked about (e.g. an all-time or 'this year' question).",
    )
    year: Optional[int] = Field(
        default=None,
        description="The year that goes with month_name (or a bare year if no month is named), resolving "
        "relative terms against today's real date. Null if no specific year/month is being asked about.",
    )


def _resolve_flow_intent(question: str) -> Optional[_FlowQuestionIntent]:
    """Independently re-derives what a flow-related question is actually
    asking - from the question's own words, never from the SQL the agent
    happened to write - so this verification can't inherit whatever
    mistake the agent's own query made. Same principle as
    _resolve_live_data_request(). Fails open (None) on any error."""
    llm = get_llm("ANALYTICS")
    structured_llm = llm.with_structured_output(_FlowQuestionIntent)
    system_text = (
        f"Today's real date is {date.today().isoformat()}. Determine what this question about andSons "
        "CRM/business data is actually asking, precisely enough to build the exact right database filter."
    )
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", "Question: {question}")])
    chain = prompt | structured_llm
    try:
        return _invoke_with_retry(chain, {"question": question}, label="Flow-intent resolution call")
    except Exception as exc:  # noqa: BLE001 - fail open, no correction attempted
        logger.warning("Flow-intent resolution failed for %r: %s", question, exc)
        return None


def _verify_flow_orders_answer(question: str, answer: str, sql_query: str) -> Optional[str]:
    """Deterministic safety net for the flow_orders view specifically - a
    prompt instruction alone couldn't reliably guarantee the agent applies
    BOTH is_flow_attributed/flow_family AND new_product_category correctly
    together on an unfamiliar cross-project view (confirmed live: the exact
    same question, same code, produced the right answer on some runs and a
    ~40x-too-large wrong one on others). Rather than trying to detect what
    the agent's own query got wrong, independently rebuild the correct
    query from the ORIGINAL QUESTION and compare - this can't inherit a
    mistake the agent's SQL made, because it never reads that SQL's filter
    logic at all. Fails open (None - no correction) whenever the question's
    intent can't be confidently resolved, rather than risk correcting with
    the wrong scope."""
    if "flow_orders" not in sql_query:
        return None
    try:
        db = _get_db_cached()
    except Exception:
        return None

    intent = _resolve_flow_intent(question)
    if intent is None or not intent.is_flow_question:
        return None
    if intent.wants_breakdown:
        # Real, live-caught bug this guards against: several genuinely
        # correct, properly-grouped breakdown answers (per-brand revenue,
        # per-flow-type revenue, a WoW/MoM trend) got silently discarded
        # and replaced with one irrelevant blanket total, because this
        # check's only correction mechanism is a SINGLE number - which can
        # never represent or fix a multi-row breakdown answer. A breakdown
        # question needs a real per-group check to verify at all (not built
        # here) - until then, this must fail open rather than actively make
        # a detailed, correct answer worse.
        return None
    if intent.metric == "other":
        # Real bug this guards against, caught live: this check used to
        # ALWAYS compute SUM(Final_Revenue) regardless of what the question
        # actually asked for - a plain order-count question ("how many
        # orders came from winback") got its correct integer answer
        # silently overwritten with an unrelated revenue figure, because
        # the only thing being compared was "does any number in the answer
        # match the revenue total", not "is revenue even the right metric
        # for this question". Anything this specific check doesn't know how
        # to independently compute (average order value, unique customers,
        # a ratio) must fail open, never force-fit a revenue correction
        # onto a question about something else entirely.
        return None
    named_flow_family = _normalize_flow_family(intent.named_flow_family)
    if not intent.wants_all_flows and not named_flow_family:
        return None  # ambiguous which flow - don't guess, don't correct
    category_code = _normalize_category_code(intent.product_category_code) or _detect_category_code_in_text(question)
    _VALID_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
                      "August", "September", "October", "November", "December")
    month_name = intent.month_name.strip().capitalize() if intent.month_name else None
    if month_name not in _VALID_MONTHS:
        month_name = None
    year = int(intent.year) if intent.year else None
    if month_name and not year:
        # Same real, verified trap as the main agent's own YEAR RESOLUTION
        # rule (see BIGQUERY_SCHEMA_NOTES) - never guess a year for a
        # month-only reference, including here in the classifier's own
        # output. Confirmed live: leaving year unresolved silently summed
        # a month across every year in the warehouse (2021-present)
        # instead of the one real year being asked about, corrupting the
        # very check meant to catch exactly this failure mode. Look up the
        # most recent year with real data for this scope directly.
        try:
            # Extract Brand and Country from the ORIGINAL query for correct scope
            extracted_brand, extracted_country = _extract_brand_country_from_sql(sql_query)
            brand = extracted_brand or "AndSons"
            country = extracted_country or "Singapore"

            year_probe = [f"Brand='{brand}'", f"Country='{country}'", f"Month_Name='{month_name}'"]
            if category_code:
                year_probe.append(f"new_product_category='{category_code}'")
            year_result = db.run(
                "SELECT MAX(Year) FROM `crm-mail-automation-dev.crm_analytics_views.flow_orders` WHERE "
                + " AND ".join(year_probe)
            )
            year_match = re.search(r"\d{4}", str(year_result))
            if year_match:
                year = int(year_match.group())
        except Exception:
            logger.warning("Year lookup for month-only flow_orders check failed - proceeding without a year filter.")

    # Extract Brand and Country from the ORIGINAL query so verification uses the same scope
    extracted_brand, extracted_country = _extract_brand_country_from_sql(sql_query)
    brand = extracted_brand or "AndSons"
    country = extracted_country or "Singapore"

    where = [f"Brand='{brand}'", f"Country='{country}'"]
    period = ""
    if year:
        where.append(f"Year={year}")
        period = f" for {year}"
    if month_name:
        where.append(f"Month_Name='{month_name}'")
        period = f" for {month_name} {year}" if year else f" for {month_name}"
    if category_code:
        where.append(f"new_product_category='{category_code}'")
    where.append("NOT is_excluded_status")
    if intent.wants_all_flows:
        where.append("is_flow_attributed")
    else:
        where.append(f"flow_family='{named_flow_family}'")

    # Which real aggregate to check against depends on intent.metric,
    # resolved above from the question's own words - never assume revenue
    # regardless of what was actually asked (see the metric field's
    # docstring for the real incident this fixes).
    if intent.metric == "order_count":
        select_sql = "SELECT COUNT(DISTINCT order_id) AS total FROM `crm-mail-automation-dev.crm_analytics_views.flow_orders` "
    else:
        select_sql = "SELECT ROUND(SUM(Final_Revenue),2) AS total FROM `crm-mail-automation-dev.crm_analytics_views.flow_orders` "

    try:
        canonical_result = db.run(select_sql + "WHERE " + " AND ".join(where))
        match = re.search(r"[-\d.]+", str(canonical_result))
        if not match:
            # A genuine NULL/no-rows result (str(canonical_result) has no
            # digits at all, e.g. "[(None,)]") - not an error, just nothing
            # to correct against.
            return None
        canonical_total = float(match.group())
    except Exception:
        logger.exception("flow_orders cross-check query failed for intent %r - skipping correction.", intent)
        return None

    stated_numbers = [float(n) for n in _extract_numbers(answer)]
    if any(_close(canonical_total, n) for n in stated_numbers):
        return None  # the agent's own answer already matches the independently-derived correct total

    logger.warning(
        "flow_orders mismatch for question %r (metric=%s): stated answer had %s, independently-derived "
        "correct total is %s - correcting.",
        question, intent.metric, stated_numbers, canonical_total,
    )
    scope_desc = named_flow_family if named_flow_family else "all flow-attributed"
    if intent.metric == "order_count":
        return f"{int(round(canonical_total)):,} orders ({scope_desc}, independently verified){period}."
    return f"SGD {canonical_total:,.2f} ({scope_desc} revenue, independently verified){period}."


class _ResolvedQuestion(BaseModel):
    standalone_question: str = Field(
        description="The follow-up question rewritten as a complete, standalone question that makes "
        "sense with zero prior context - fill in whatever it's implicitly referring to from the "
        "conversation. Critically: if the follow-up narrows/filters the previous question (by product, "
        "category, channel, time period, etc.), keep the SAME metric/topic as before, just add the new "
        "filter - e.g. after 'how are winback email open rates doing', a follow-up 'what about for hair "
        "loss specifically' resolves to 'how are winback email open rates doing for hair loss "
        "specifically', NOT a switch to an unrelated metric like revenue or order counts just because "
        "'hair loss' also appears in other data. If the follow-up is already a complete standalone "
        "question (doesn't reference prior context), return it unchanged."
    )


def _resolve_followup_question(question: str, conversation_history: list, llm) -> str:
    """Rewrites a follow-up ('what about for hair loss specifically') into a
    complete standalone question using conversation history - a standard
    technique for conversational Q&A, and more reliable than asking one
    single downstream prompt to juggle history-resolution, topic
    continuity, SQL tool-calling, and MoEngage-vs-BigQuery source selection
    all at once. Caught live: without this, follow-ups that narrowed a
    metric by category silently swapped to a different, unrelated metric
    instead of staying on-topic. Fails open (returns the question
    unresolved) rather than blocking the whole answer on a rewrite failure."""
    history_text = "\n\n".join(f"Q: {h['question']}\nA: {h['answer']}" for h in conversation_history)
    structured_llm = llm.with_structured_output(_ResolvedQuestion)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Conversation so far:\n---\n{history}\n---"),
        ("human", "Follow-up: {question}"),
    ])
    chain = prompt | structured_llm
    try:
        result: _ResolvedQuestion = _invoke_with_retry(
            chain, {"history": history_text, "question": question}, label="Follow-up resolution call",
        )
        return result.standalone_question
    except Exception as exc:  # noqa: BLE001 - fail open, the raw question still works on its own
        logger.warning("Failed to resolve follow-up question %r against history: %s", question, exc)
        return question


class _MoEngageExclusive(BaseModel):
    moengage_only: bool = Field(
        description="True ONLY if answering this question needs NOTHING that a database query could "
        "provide - no revenue, no order count, no customer count, no spend, and no per-campaign opens/ "
        "clicks/CVR/unsubscribe/control-group metric either, since those now live in real, exact BigQuery "
        "tables too (see schema notes: moengage_campaigns_email/whatsapp/push, moengage_flows_summary) - "
        "prefer that real, queryable source over the MoEngage daily-pull tool whenever a question could be "
        "answered either way (that source is exact and queryable; the daily-pull tool is real but only as "
        "current as the most recent daily run). This should be True "
        "mainly for genuine node-level detail on a specific flow those tables don't capture. False if answering it needs a "
        "database query for anything, even partially, or if you're genuinely not sure."
    )


def _is_moengage_exclusive(question: str, llm) -> bool:
    """Only called once MoEngage relevance is already confirmed (see
    gather_moengage_context) - decides whether the SQL agent needs to run
    AT ALL for this specific question, so a genuinely MoEngage-only
    question (e.g. 'what's our winback open rate') can skip BigQuery
    entirely instead of it running unconditionally on every question
    regardless of relevance. Real gap this fixes: MoEngage already had a
    real relevance gate (gather_moengage_context's pre-check) before this
    existed, but BigQuery's SQL agent had none at all - it built and ran
    the full ReAct loop on every single question, even ones with nothing
    for a database to answer, relying entirely on a prompt instruction
    ('don't run SQL as a substitute') to keep it from padding the answer
    with an irrelevant query - the same class of reliability gap this
    codebase's own deterministic safety nets exist to close everywhere
    else. Fails closed (False - query BigQuery too) on any error, since
    BigQuery is this system's broad default source and skipping it
    wrongly is a worse mistake than an unnecessary query."""
    structured_llm = llm.with_structured_output(_MoEngageExclusive)
    prompt = ChatPromptTemplate.from_messages([("human", "Question: {question}")])
    chain = prompt | structured_llm
    try:
        return _invoke_with_retry(chain, {"question": question}, label="MoEngage-exclusive check").moengage_only
    except Exception as exc:  # noqa: BLE001 - fail closed to the safer default (query BigQuery too)
        logger.warning("MoEngage-exclusive check failed for %r: %s - querying BigQuery too, to be safe.", question, exc)
        return False


class _ChannelIntent(BaseModel):
    channel_meaning: Literal["marketplace", "paid_ad_platform", "crm_attribution", "not_a_channel_question"] = Field(
        description="Which real, different thing the word 'channel'/'Channel' means in THIS question, if it "
        "uses that word at all. 'marketplace' - which storefront/platform an order was physically placed on "
        "(Dotcom's own site vs Shopee vs Lazada vs Zalora vs TikTok Shop) - use when the question is about "
        "where things are SOLD, sales channels, storefronts, or marketplaces by name. 'paid_ad_platform' - "
        "which paid advertising platform spend/clicks went to (Facebook, Google, Bing, TikTok, Quora, "
        "Snapchat, Reddit) - use when the question is explicitly about ad spend, paid acquisition, or names "
        "an ad platform. 'crm_attribution' - the order-level CRM/lifecycle-marketing attribution signal "
        "(email, WhatsApp, CRM sends, banners, organic/direct, paid search/social by medium) - use for a "
        "bare 'which channels are driving sales/revenue' question in a CRM, lifecycle-marketing, or "
        "cross-brand/cross-market comparison context, which is the default reading unless the question "
        "specifically names marketplaces or paid ads. 'not_a_channel_question' if the question doesn't turn "
        "on this word's meaning at all."
    )


def _resolve_channel_intent(question: str) -> str:
    """Deterministic pre-classifier for the single most repeated real bug
    this whole project has hit: a bare 'channel' question answered from the
    wrong one of three genuinely different real tables/columns that happen
    to share that column name. A prompt note describing the disambiguation
    was NOT enough on its own - confirmed live, repeatedly, the SAME exact
    question text landing on a different (sometimes wrong) table across
    different runs, because a 25-iteration free-form ReAct loop doesn't
    reliably re-apply one paragraph of guidance every single time. This
    mirrors the existing _resolve_metric_intent/wants_breakdown pattern:
    make ONE focused, structured-output classifier call resolve the
    ambiguity ONCE, deterministically, before the SQL agent ever starts,
    then hand it the answer as a direct instruction instead of hoping it
    re-derives the same judgment call correctly mid-exploration. Returns
    'not_a_channel_question' (i.e. no injection needed) on any resolution
    failure - fails open, never blocks or forces a table choice it isn't
    confident about."""
    llm = get_llm("ANALYTICS")
    structured_llm = llm.with_structured_output(_ChannelIntent)
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "Determine which real meaning of 'channel' (if any) this question about andSons business data "
            "is actually asking about.",
        ),
        ("human", "Question: {question}"),
    ])
    chain = prompt | structured_llm
    try:
        result = _invoke_with_retry(chain, {"question": question}, label="Channel-intent resolution call")
        return result.channel_meaning
    except Exception as exc:  # noqa: BLE001 - fail open, no injection rather than block the question
        logger.warning("Channel-intent resolution failed for %r: %s - leaving unresolved.", question, exc)
        return "not_a_channel_question"


_CHANNEL_MEANING_INSTRUCTIONS = {
    "marketplace": (
        "CHANNEL MEANING RESOLVED FOR THIS QUESTION: 'channel' here means the real MARKETPLACE/storefront an "
        "order was placed on. Use dotcom_plus_marketplace.Channel (or updated_sales_data.Channel) - real "
        "values: Dotcom, Shopee, Lazada, Zalora, TikTok. Do not use marketing_data/marketing_clicks_data/"
        "marketing_spend_data's Channel (paid ad platforms) or orders_utm_medium (CRM attribution) for this "
        "specific question - those answer a different question."
    ),
    "paid_ad_platform": (
        "CHANNEL MEANING RESOLVED FOR THIS QUESTION: 'channel' here means the real PAID ADVERTISING PLATFORM "
        "spend/clicks went to. Use marketing_data/marketing_clicks_data/marketing_spend_data.Channel - real "
        "values: Facebook, Google, Bing, TikTok, Quora, Snapchat, Reddit. Do not use "
        "dotcom_plus_marketplace.Channel (marketplaces) or orders_utm_medium (CRM attribution) for this "
        "specific question - those answer a different question."
    ),
    "crm_attribution": (
        "CHANNEL MEANING RESOLVED FOR THIS QUESTION: 'channel' here means the real CRM/lifecycle-marketing "
        "attribution signal. Use updated_sales_data.orders_utm_medium (or flow_orders' same column for a "
        "flow-specific question) - real values include email, whatsapp, crm, banner, cpc, social, direct/"
        "organic (case varies, match with LOWER()). Do not use dotcom_plus_marketplace.Channel "
        "(marketplaces) or marketing_data's Channel (paid ad platforms) for this specific question - those "
        "answer a different question, even though they share the same column name."
    ),
}


def ask_analytics(
    question: str, conversation_history: Optional[list] = None, file_context: Optional[str] = None
) -> dict:
    """conversation_history, if given, is a list of {"question": ..., "answer": ...}
    dicts from earlier turns in the same thread/session - used only to resolve
    context ("the same", "that flow", "what about X instead"), never as a
    source of numbers. The model is explicitly told to always recompute the
    actual answer with a fresh query rather than reuse a figure from an
    earlier turn, so the number-grounding guardrail below still applies in
    full to every answer regardless of history.

    file_context, if given (a summary from file_context.summarize_files - a
    file uploaded alongside the question), is real data too - the model may
    cite numbers from it directly (the number-verification guardrail below
    checks against BOTH the SQL tool results AND this file context, so a
    figure genuinely from the uploaded file still passes)."""
    # No pre-check against the raw human question text here (there used to
    # be one) - real, live-caught false positive: "Where is the biggest
    # funnel drop-off?" got refused outright as an attempted write
    # operation, because \bDROP\b matches the ordinary English word "drop"
    # inside "drop-off" just as readily as it matches the SQL keyword. That
    # check added no real protection anyway - the actual guardrails are the
    # read-only DB connection (writes physically fail regardless of what
    # anyone asks) and _contains_write_operation(sql_query) below, checked
    # against the real SQL the agent actually tried to run, where these
    # keywords can't appear as innocuous prose the way they can in a human
    # question.
    llm = get_llm("ANALYTICS")

    # Resolve a follow-up ("what about for hair loss specifically") into a
    # complete standalone question BEFORE anything else - this is what the
    # source-selection reasoning below, the SQL agent, and number-
    # verification all actually work from, so topic continuity is settled
    # once up front rather than re-litigated (unreliably) inside one giant
    # combined prompt. effective_question is used for processing; `question`
    # (the human's literal text) is still what gets logged to history.
    effective_question = question
    if conversation_history:
        effective_question = _resolve_followup_question(question, conversation_history, llm)
        if effective_question != question:
            logger.info("Resolved follow-up %r -> %r", question, effective_question)

    # SOURCE SELECTION - reasoned, not a hard constraint to query both: real
    # gap this closes (previously) - MoEngage already had a genuine
    # relevance gate (gather_moengage_context's own pre-check), but BigQuery
    # had none at all, so the SQL agent ran unconditionally on every single
    # question regardless of whether a database had anything to do with it.
    # gather_moengage_context decides MoEngage relevance first (cheap
    # pre-check before paying the ~40-50s full-fetch cost); when it IS
    # relevant, _is_moengage_exclusive then decides whether BigQuery is
    # needed AT ALL for this specific question, so a genuinely
    # MoEngage-only question can skip the SQL agent entirely rather than
    # relying purely on a prompt instruction to keep it from padding the
    # answer with an irrelevant query.
    moengage_context, moengage_used, moengage_raw_summary, moengage_checked = gather_moengage_context(effective_question, llm)
    skip_bigquery = moengage_used and _is_moengage_exclusive(effective_question, llm)

    if skip_bigquery:
        raw_answer = sanitize_text(moengage_raw_summary)
        if _contains_pii(raw_answer):
            return {"answer": PII_BLOCKED_MESSAGE, "sql_query": "", "verified": False, "data_source": "moengage", "moengage_used": True}
        verified = _verify_numbers(raw_answer, moengage_context)
        answer = raw_answer if verified else (
            "I couldn't verify that figure - the number in my draft answer didn't trace back to a real MoEngage result."
        )
        return {"answer": answer, "sql_query": "", "verified": verified, "data_source": "moengage", "moengage_used": True}

    # Only reachable once source selection above has already decided this
    # question genuinely needs the database - a BigQuery outage no longer
    # blocks a question the database was never going to be needed for.
    try:
        db = _get_db_cached()
    except Exception:
        logger.exception("BigQuery connection unavailable.")
        return {
            "answer": "I can't reach the live BigQuery connection right now, so I can't answer that. "
            "This usually means the credentials or IAM permission need attention.",
            "sql_query": "",
            "verified": False,
            "data_source": "bigquery",
            "moengage_used": moengage_used,
        }

    toolkit = SQLDatabaseToolkit(db=db, llm=llm)

    system_prefix = SYSTEM_PREFIX_TEMPLATE.format(
        today=date.today().isoformat(), schema_notes=BIGQUERY_SCHEMA_NOTES + WAREHOUSE_SCHEMA_NOTES
    )

    agent_executor = create_sql_agent(
        llm=llm,
        toolkit=toolkit,
        agent_type="tool-calling",
        prefix=system_prefix,
        verbose=False,
        # The 4 real warehouse_* tools defined above - a second, separate
        # real production data source alongside BigQuery, added here (not
        # as a second agent) so one answer can freely combine both when a
        # question genuinely needs to.
        extra_tools=[warehouse_brand_status, warehouse_list_tables, warehouse_table_schema, warehouse_query],
        # max_iterations: real gap this raises - LangChain's own default (15)
        # was too low once real, verified, live-caught: a question needing
        # exploration across the 3 new real per-campaign MoEngage tables
        # (checking which real flow names exist, per table, before
        # aggregating) genuinely used every step correctly and still ran
        # out before producing a final answer, falling back to "couldn't
        # find data" despite already having computed the real number it
        # needed. More tables to reason over needs more budget to do it in,
        # not a smaller one. create_sql_agent's own top-level max_iterations
        # param, not agent_executor_kwargs - it passes max_iterations into
        # the AgentExecutor itself internally, so setting it again inside
        # agent_executor_kwargs is a genuine duplicate-keyword conflict
        # (caught live: TypeError on every single call once added there).
        max_iterations=25,
        agent_executor_kwargs={"return_intermediate_steps": True},
    )

    agent_input = (
        "Never copy a number from prior knowledge - always compute the answer with a fresh query:\n"
        + effective_question
    )

    channel_meaning = _resolve_channel_intent(effective_question)
    if channel_meaning in _CHANNEL_MEANING_INSTRUCTIONS:
        agent_input = _CHANNEL_MEANING_INSTRUCTIONS[channel_meaning] + "\n\n" + agent_input

    if file_context:
        agent_input = (
            "A file was uploaded alongside this question - real data, safe to cite directly if it "
            "answers the question. Combine it with the database when relevant (e.g. the file lists "
            "products, the database has revenue for them):\n---\n" + file_context + "\n---\n\n" + agent_input
        )

    if moengage_used:
        agent_input = (
            "Real MoEngage campaign/engagement data relevant to this question, given to you directly "
            "below FROM A LIVE FLOW-DUMP TOOL (a fresh full-account pull, run just now for this question) "
            "- a separate, narrower source than the real "
            "moengage_campaigns_email/whatsapp/push/flows_summary BigQuery tables (see schema notes), "
            "which now hold real, exact per-campaign opens/clicks/CVR/unsubscribe/control-group numbers - "
            "PREFER those tables via SQL for anything they cover (they give an exact queried number "
            "without paying this tool's real cost); use this live context below only for genuine detail "
            "those tables don't have (node-level detail on a specific named flow). "
            "USE IT: if it answers something the tables genuinely don't, cite it directly - do not say "
            "that data isn't available if this context already shows it. Still run SQL for anything this "
            "doesn't cover (revenue, order counts, or the exact per-campaign metrics above), and "
            "combine both ONLY when the question genuinely needs both. A real, serious mistake this "
            "caused before: a plain 'how did automation perform this year' question, already fully and "
            "cleanly answered by one SQL revenue/order total, got padded out with several unrelated "
            "single-flow snippets (send/open counts for named flows the question never asked "
            "about) glued on with no stated relationship to the SQL total - a reader can't tell if those "
            "numbers are included in, separate from, or overlapping with the real total, which makes the "
            "whole answer impossible to trust. If the SQL total alone actually answers the question, stop "
            "there - do not append MoEngage detail just because it happens to be available. Only bring in "
            "a MoEngage number when it covers something SQL genuinely can't (opens/clicks/delivery rate), "
            "and when you do, state plainly which exact time period and population it covers so it's "
            "never confused with a different total in the same answer. Also: MoEngage/BigQuery 'ATM_' "
            "flows are automation broadly (WhatsApp AND email AND other channels) - never call them "
            "'automated email flows' collectively unless the question is specifically about the email "
            "channel; call them 'automated/CRM flows' otherwise.\n---\n"
            + moengage_context + "\n---\n\n" + agent_input
        )
    elif moengage_checked:
        # Real gap this closes: MoEngage's own live flow dump was
        # genuinely checked (not skipped) and came back with nothing
        # relevant to THIS question - but without telling the SQL agent
        # that a real check happened, it has no way to know, and ends up
        # answering purely from BigQuery's own perspective ("no email-
        # engagement tables in the data warehouse") in a way that reads as
        # if MoEngage was never considered at all, when it genuinely was.
        # Caught live: "is list health deteriorating" got exactly this
        # vague, misleading answer even after a real MoEngage check
        # confirmed no unsubscribe/complaint/bounce metric exists
        # anywhere in the real workspace - the honest, specific version of
        # that same true fact ("MoEngage doesn't track this specific
        # metric") is what should reach the final answer, not a generic
        # "not in the data warehouse" that implies no one looked.
        agent_input = (
            "MoEngage was already checked live for this question and found nothing "
            "relevant - the real reason, verbatim, is below. If your final answer touches anything that "
            "reason covers, state that SPECIFIC reason plainly (e.g. 'MoEngage doesn't track that as its "
            "own metric' or whatever the real reason says) - never say generically that the data 'isn't in "
            "the database' or 'isn't available' when a real, specific check already ran and found a real, "
            "specific reason.\n---\n" + moengage_context + "\n---\n\n" + agent_input
        )

    # Real, repeatedly-observed failure mode (same class already fixed for
    # every Copywriter/Sweeper call via invoke_with_retry): a transient Groq
    # tool-calling hiccup mid-ReAct-loop ("Failed to parse tool call
    # arguments as JSON", "attempted to call tool X which was not in
    # request.tools") used to crash this whole call with zero retry - the
    # caller's broad except still caught it and told the human "something
    # went wrong", but a real, answerable question shouldn't need a second
    # manual attempt just because of infra flakiness. Retry here too,
    # same principle, before giving up with a graceful message instead of
    # letting the exception propagate.
    result = None
    last_exc = None
    for attempt in range(3):
        try:
            result = agent_executor.invoke({"input": agent_input})
            break
        except Exception as exc:  # noqa: BLE001 - every attempt logged, final one falls through gracefully
            last_exc = exc
            logger.warning("Analytics SQL agent call failed (attempt %d/3): %s", attempt + 1, exc)
    # Real base label - refined below, after intermediate_steps is read, to
    # also reflect real warehouse_* tool usage (see that loop for why this
    # can't be decided here yet).
    data_source = "bigquery+moengage" if moengage_used else "bigquery"

    if result is None:
        logger.error("Analytics SQL agent failed after 3 attempts: %s", last_exc)
        return {
            "answer": "I hit a technical error trying to answer that - worth trying again in a moment, "
            "or rephrasing the question.",
            "sql_query": "",
            "verified": False,
            "data_source": data_source,
            "moengage_used": moengage_used,
        }

    raw_output = _extract_output_text(result.get("output", "")).strip()
    if raw_output.lower().startswith("agent stopped due to") or not raw_output:
        # Blank output is a real, separate way this can go wrong from the
        # "agent stopped due to..." message (e.g. the agent's last step
        # produced no final text) - both get the same graceful fallback
        # rather than a blank answer silently passing every check below
        # (an empty string has no numbers to verify, so it would otherwise
        # come back marked verified=True).
        raw_output = (
            "I couldn't find data to answer that question with what's available in this database. "
            "It may be tracked in a different system, or the question may need to be more specific."
        )
    raw_answer = sanitize_text(raw_output)
    intermediate_steps = result.get("intermediate_steps", [])

    executed_queries = []
    query_observations = []  # (query_text, observation_text) - real SQL calls only, in run order
    warehouse_used = False
    warehouse_observations = []  # every real warehouse_* tool's own observation text (brand_status/
    # list_tables/table_schema included, not just warehouse_query) - a number an answer cites from e.g.
    # warehouse_brand_status's real reachability report ("1 of 8 brands reachable") is exactly as real and
    # verifiable as one from a query result; excluding these would wrongly fail verification on it.
    for action, observation in intermediate_steps:
        tool_name = getattr(action, "tool", "")
        tool_input = getattr(action, "tool_input", "")
        obs_text = str(observation)
        if tool_name.startswith("warehouse_"):
            warehouse_used = True
            warehouse_observations.append(obs_text)
        if "query" in tool_name.lower() and "checker" not in tool_name.lower() and "list" not in tool_name.lower() and "schema" not in tool_name.lower():
            # BigQuery's own sql_db_query tool uses {"query": ...}; the
            # warehouse_query tool (see WAREHOUSE tools section above) uses
            # {"brand": ..., "sql": ...} instead - a real, live-caught gap
            # this fixes: without the "sql" fallback, any number sourced
            # purely from a warehouse_query call had NO evidence text to
            # verify against, so _verify_numbers always failed it, even
            # though the number itself was genuinely real.
            if isinstance(tool_input, dict):
                query = tool_input.get("query") or tool_input.get("sql")
            else:
                query = tool_input
            if query:
                executed_queries.append(str(query))
                query_observations.append((str(query), obs_text))

    sql_query = "\n\n".join(executed_queries)
    if warehouse_used:
        data_source = data_source.replace("bigquery", "bigquery+warehouse") if "bigquery" in data_source else data_source + "+warehouse"

    if _contains_write_operation(sql_query):
        return {"answer": WRITE_BLOCKED_MESSAGE, "sql_query": sql_query, "verified": False, "data_source": data_source, "moengage_used": moengage_used}

    if _contains_pii(raw_answer):
        return {"answer": PII_BLOCKED_MESSAGE, "sql_query": sql_query, "verified": False, "data_source": data_source, "moengage_used": moengage_used}

    # Real, live-caught incident this fixes (2026-08-24, "hallucinating the
    # numbers" flagged directly by a real user): the OLD check below built
    # its evidence from the ENTIRE exploration trail - every query the agent
    # ever ran this turn, including early probes it explored and then moved
    # on from. A real, genuine number from one of those abandoned early
    # queries (e.g. a total with no Brand filter, run before the agent
    # settled on the correctly-filtered query it actually displayed) still
    # legitimately "appears in the tool trace", so the old exists-anywhere
    # check passed it - even though the final answer misattributed that
    # number to a different, narrower scope than the query that actually
    # produced it. That is exactly as much a hallucination as inventing a
    # number outright: a real number, wrongly labeled. Fix: scope the
    # PRIMARY grounding evidence to only the most recent query results (the
    # ones the agent actually converged on), not the full history - a
    # number that only exists in an early, superseded query no longer
    # grounds anything. Deliberately no fallback to the full trail on
    # failure - that would silently reopen the same loophole. Window size:
    # the later half of however many real queries were run this turn (min
    # 4) - generous enough that a legitimate answer synthesizing several of
    # its OWN final queries still passes, narrow enough to exclude early,
    # abandoned exploration in a longer multi-step trace.
    recent_window = max(4, -(-len(query_observations) // 2))  # ceil(n/2), floor 4
    recent_evidence_text = "\n".join(
        f"{q}\n{obs}" for q, obs in query_observations[-recent_window:]
    )
    if file_context:
        recent_evidence_text += "\n" + file_context
    if moengage_used:
        recent_evidence_text += "\n" + moengage_context
    if warehouse_observations:
        recent_evidence_text += "\n" + "\n".join(warehouse_observations)

    verified = _verify_numbers(raw_answer, recent_evidence_text)
    if verified:
        answer = raw_answer
    else:
        answer = "I couldn't verify that figure - the number in my draft answer didn't trace back to a query result."

    # Deterministic safety net for the campaign-name-sprawl failure mode - a
    # prompt instruction alone can't guarantee a probabilistic SQL agent
    # never builds an incomplete filter, so re-check independently rather
    # than trust it. Runs regardless of the `verified` outcome above (a
    # partial-match answer traces back to a real query result, so the
    # generic check alone wouldn't have caught it either).
    correction = _verify_campaign_family_total(effective_question, answer, sql_query)
    if correction:
        answer = correction
        verified = True

    # Second, broader safety net specifically for the flow_orders view -
    # unlike the check above (which reverse-engineers the agent's own SQL),
    # this independently re-derives the correct answer from the question
    # itself, so it catches mistakes the agent's SQL made that still look
    # internally consistent (e.g. correctly using is_flow_attributed but
    # forgetting new_product_category, or vice versa).
    view_correction = _verify_flow_orders_answer(effective_question, answer, sql_query)
    if view_correction:
        answer = view_correction
        verified = True

    return {
        "answer": answer,
        "sql_query": sql_query,
        "verified": verified,
        "data_source": data_source,
        "moengage_used": moengage_used,
    }
