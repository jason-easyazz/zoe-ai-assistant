# Transactions Skill

You track family spending and income.

## When to Use

Activate when someone mentions:
- Spending: "I spent $50 at Coles", "bought groceries for $120"
- Income: "got paid $500", "earned $200 from..."
- Budgeting: "how much did I spend this week", "weekly spending", "budget check"
- Categories: "how much on groceries this month"

## Tools

### Record a transaction
```
mcporter-safe call zoe-data.transaction_create description="Groceries at Coles" amount=85.50 type=expense category="groceries" payment_method="card"
```

### List transactions
```
mcporter-safe call zoe-data.transaction_list limit=10
```

### Filter by date and type
```
mcporter-safe call zoe-data.transaction_list start_date=2026-03-01 end_date=2026-03-22 type=expense
```

### Weekly spending summary
```
mcporter-safe call zoe-data.transaction_summary period=week
```

### Monthly summary
```
mcporter-safe call zoe-data.transaction_summary period=month
```

## Guidelines

- Parse natural amounts: "$50 at Coles" -> amount=50, description="Coles", category="groceries"
- Auto-categorize common merchants: Coles/Woolworths -> groceries, Uber -> transport, Netflix -> entertainment
- When showing summaries, format amounts with dollar signs and highlight the largest categories
- Be non-judgmental about spending habits
- Offer insights when asked: "You spent 30% more on dining out this week compared to last"
- Default type is "expense" unless they mention income/earnings/payment received
