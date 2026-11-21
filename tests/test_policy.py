from app.services import orders, policy

# Expected values below match tokri_Database.xlsx's own precomputed
# max_discount_pct/tier/churn_risk/credit_blocked columns exactly - each
# customer id corresponds 1:1 to that sheet's row order (C001..C010).


def test_gold_healthy_baseline(fresh_db):
    prof = policy.profile(orders.get_customer(1))  # Sunita Rao
    assert prof["tier"] == "Gold"
    assert prof["churn_risk"] is False
    assert prof["max_discount_pct"] == 5.0


def test_gold_at_churn_risk_gets_winback_cap(fresh_db):
    prof = policy.profile(orders.get_customer(2))  # Meera Nair
    assert prof["tier"] == "Gold"
    assert prof["churn_risk"] is True
    assert prof["max_discount_pct"] == 7.0


def test_gold_but_credit_blocked_caps_at_3_pct(fresh_db):
    prof = policy.profile(orders.get_customer(3))  # Lakshmi Devi, outstanding == limit
    assert prof["credit_blocked"] is True
    assert prof["credit_available"] == 0
    assert prof["max_discount_pct"] == 3.0
    assert prof["prepay_only"] is True


def test_silver_healthy_baseline(fresh_db):
    prof = policy.profile(orders.get_customer(4))  # Prakash Hegde
    assert prof["tier"] == "Silver"
    assert prof["max_discount_pct"] == 4.0


def test_silver_at_churn_risk_gets_winback_cap(fresh_db):
    prof = policy.profile(orders.get_customer(5))  # Ramesh Kumar
    assert prof["churn_risk"] is True
    assert prof["max_discount_pct"] == 7.0


def test_credit_blocked_ceiling_overrides_winback_cap(fresh_db):
    # Divya Shetty: Silver + churn risk (would be 7%) + credit blocked
    # (on-time 65% < 70%) - the blocked ceiling wins: min(3%, 7%) = 3%.
    prof = policy.profile(orders.get_customer(6))
    assert prof["churn_risk"] is True
    assert prof["credit_blocked"] is True
    assert prof["max_discount_pct"] == 3.0


def test_new_customer_no_history(fresh_db):
    prof = policy.profile(orders.get_customer(7))  # Anita Sharma
    assert prof["tier"] == "New"
    assert prof["max_discount_pct"] == 2.0
    assert prof["prepay_only"] is True


def test_credit_blocked_ceiling_cannot_raise_a_new_customers_cap(fresh_db):
    # Vikram Joshi: New (2%) + credit blocked (would otherwise be 3%) -
    # the ceiling can only ever lower a cap, never raise it: min(3%, 2%) = 2%.
    prof = policy.profile(orders.get_customer(8))
    assert prof["tier"] == "New"
    assert prof["credit_blocked"] is True
    assert prof["max_discount_pct"] == 2.0


def test_incomplete_profile_is_forced_to_new_prepay_only(fresh_db):
    # Farhan Ali is missing language - until asked, treat as New/prepay-only
    # regardless of what the raw signals might otherwise compute to.
    prof = policy.profile(orders.get_customer(9))
    assert prof["data_status"] == "INCOMPLETE"
    assert prof["tier"] == "New"
    assert prof["prepay_only"] is True
    assert "language" in prof["agent_question"]


def test_fully_incomplete_profile_also_forced_to_new(fresh_db):
    prof = policy.profile(orders.get_customer(10))  # Rohit Verma
    assert prof["data_status"] == "INCOMPLETE"
    assert prof["tier"] == "New"
    assert prof["agent_question"]
