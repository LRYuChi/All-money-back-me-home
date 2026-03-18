---
name: stripe-best-practices
description: Guides Stripe integration decisions - API selection (Checkout Sessions vs PaymentIntents), Connect platform setup (Accounts v2, controller properties), billing/subscriptions, Treasury financial accounts, integration surfaces (Checkout, Payment Element), and migrating from deprecated Stripe APIs. Use when building, modifying, or reviewing any Stripe integration.
source: https://github.com/stripe/ai/tree/main/skills/stripe-best-practices
---

Latest Stripe API version: **2026-02-25.clover**. Always use the latest API version and SDK unless the user specifies otherwise.

## Integration Routing

| Building... | Recommended API | Details |
|---|---|---|
| One-time payments | Checkout Sessions | See payments section |
| Custom payment form with embedded UI | Checkout Sessions + Payment Element | See payments section |
| Saving a payment method for later | Setup Intents | See payments section |
| Connect platform or marketplace | Accounts v2 (`/v2/core/accounts`) | See connect section |
| Subscriptions or recurring billing | Billing APIs + Checkout Sessions | See billing section |
| Embedded financial accounts / banking | v2 Financial Accounts | See treasury section |

## Key Documentation

When the user's request does not clearly fit a single domain above, consult:

- [Integration Options](https://docs.stripe.com/payments/payment-methods/integration-options.md) -- Start here when designing any integration.
- [API Tour](https://docs.stripe.com/payments-api/tour.md) -- Overview of Stripe's API surface.
- [Go Live Checklist](https://docs.stripe.com/get-started/checklist/go-live.md) -- Review before launching.

---

## Payments

### Checkout Sessions (Recommended for most use cases)

Checkout Sessions provide a pre-built, hosted payment page or embeddable UI component.

**Server-side (Node.js/Python example):**
```python
import stripe

session = stripe.checkout.Session.create(
    mode="payment",
    line_items=[{
        "price_data": {
            "currency": "usd",
            "product_data": {"name": "Trading Strategy Report"},
            "unit_amount": 2999,  # $29.99
        },
        "quantity": 1,
    }],
    success_url="https://yoursite.com/success?session_id={CHECKOUT_SESSION_ID}",
    cancel_url="https://yoursite.com/cancel",
)
```

**Redirect to Checkout:**
```typescript
const response = await fetch('/api/create-checkout-session', { method: 'POST' });
const { url } = await response.json();
window.location.href = url;
```

### Payment Element (Embedded UI)

For custom payment forms embedded in your site:
```typescript
import { loadStripe } from '@stripe/stripe-js';
import { Elements, PaymentElement } from '@stripe/react-stripe-js';

const stripePromise = loadStripe('pk_live_...');

function CheckoutForm() {
  return (
    <Elements stripe={stripePromise} options={{ clientSecret }}>
      <PaymentElement />
      <button type="submit">Pay</button>
    </Elements>
  );
}
```

### Setup Intents (Save Payment Method)

Save a card for future use without charging immediately:
```python
setup_intent = stripe.SetupIntent.create(
    customer=customer_id,
    payment_method_types=["card"],
)
```

---

## Connect (Marketplaces & Platforms)

Use Accounts v2 (`/v2/core/accounts`) for new integrations:
```python
account = stripe.v2.core.accounts.create(
    controller={
        "stripe_dashboard": {"type": "none"},
        "fees": {"payer": "application"},
        "losses": {"payments": "application"},
    },
)
```

### Direct Charges vs Destination Charges

| Pattern | Use When |
|---------|----------|
| Direct charges | Platform acts as payment facilitator |
| Destination charges | Platform collects payment, sends to connected account |

---

## Billing (Subscriptions)

### Create Subscription via Checkout

```python
session = stripe.checkout.Session.create(
    mode="subscription",
    line_items=[{
        "price": "price_premium_monthly",
        "quantity": 1,
    }],
    success_url="https://yoursite.com/success",
    cancel_url="https://yoursite.com/cancel",
)
```

### Customer Portal

Enable self-service subscription management:
```python
portal = stripe.billing_portal.Session.create(
    customer=customer_id,
    return_url="https://yoursite.com/account",
)
```

---

## Webhooks

Always verify webhook signatures:
```python
import stripe

@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        raise HTTPException(status_code=400)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        # Fulfill the order
        handle_checkout_complete(session)

    return {"status": "success"}
```

### Important Webhook Events

| Event | When |
|-------|------|
| `checkout.session.completed` | Payment successful |
| `invoice.paid` | Subscription invoice paid |
| `invoice.payment_failed` | Subscription payment failed |
| `customer.subscription.updated` | Subscription changed |
| `customer.subscription.deleted` | Subscription cancelled |

---

## Best Practices

1. **Always use Checkout Sessions** for new payment integrations (not raw PaymentIntents)
2. **Implement webhooks** for payment confirmation (don't rely on redirect alone)
3. **Use idempotency keys** for all POST requests to prevent duplicate charges
4. **Store Stripe customer IDs** in your database for repeat customers
5. **Use test mode** (`sk_test_...`) during development
6. **Verify webhook signatures** to prevent spoofed events
7. **Handle errors gracefully** and show user-friendly messages
