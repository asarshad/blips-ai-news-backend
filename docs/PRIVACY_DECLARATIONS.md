# App Store Privacy Declarations

This document provides the information needed to complete privacy declarations for Apple App Store Connect and Google Play Console.

**Last updated:** February 2026  
**App version:** 1.0.1+2

---

## Summary of Data Collection

| Data Type | Collected | Purpose | Linked to Identity | Used for Tracking |
|-----------|-----------|---------|-------------------|-------------------|
| Device ID (hashed) | Yes | Functionality, Personalization | No (anonymized) | No |
| Usage Data (interactions) | Yes | Personalization | No | No |
| Diagnostic Data (crashes) | Yes | App Stability | No | No |
| AI Chat Messages | Processed, not stored | Functionality | No | No |

---

## Apple App Store Connect — Privacy Nutrition Labels

### Data Not Collected

The following data types are **NOT** collected:
- Contact Info (name, email, phone, address)
- Health & Fitness
- Financial Info
- Location (coarse or precise)
- Sensitive Info
- User Content (photos, videos, audio)
- Contacts
- Browsing History
- Search History
- Purchases

### Data Collected

#### 1. Identifiers

| Field | Value |
|-------|-------|
| **Data Type** | Device ID |
| **Is data linked to user's identity?** | No |
| **Is data used for tracking?** | No |
| **Purpose** | App Functionality |
| **Details** | One-way SHA-256 hash of IP + User-Agent. Cannot be reversed to identify user. Used for usage quotas and abuse prevention. |

#### 2. Usage Data

| Field | Value |
|-------|-------|
| **Data Type** | Product Interaction |
| **Is data linked to user's identity?** | No |
| **Is data used for tracking?** | No |
| **Purpose** | App Functionality, Product Personalization |
| **Details** | View events (>10s), shares, bookmarks, chat starts. Used to personalize feed based on topic/source preferences. |

#### 3. Diagnostics

| Field | Value |
|-------|-------|
| **Data Type** | Crash Data |
| **Is data linked to user's identity?** | No |
| **Is data used for tracking?** | No |
| **Purpose** | App Functionality |
| **Details** | Crash reports sent to Sentry in release builds only. Contains stack traces and device info, no PII. |

### Third-Party Data Sharing

| Third Party | Data Shared | Purpose |
|-------------|-------------|---------|
| Mistral AI | Chat messages, article summaries | AI chat responses |
| OpenAI (fallback) | Chat messages, article summaries | AI chat responses (fallback provider) |
| Sentry | Crash data, device info | Crash reporting |

**Note:** Chat messages are sent to AI providers transiently for response generation. They are NOT stored on our servers.

---

## Google Play Console — Data Safety

### Data Collection and Sharing

#### Device or other IDs

| Field | Value |
|-------|-------|
| **Data collected?** | Yes |
| **Data shared?** | No |
| **Is data processed ephemerally?** | No |
| **Is collection required?** | Yes (cannot be turned off) |
| **Purpose** | App functionality, Fraud prevention/security |
| **Details** | Hashed device identifier for quotas and abuse prevention |

#### App Activity — App Interactions

| Field | Value |
|-------|-------|
| **Data collected?** | Yes |
| **Data shared?** | No |
| **Is data processed ephemerally?** | No |
| **Is collection required?** | Yes |
| **Purpose** | App functionality, Personalization |
| **Details** | Content views, shares, bookmarks, chat interactions |

### Data Shared with Third Parties

| Third Party | Data Type | Purpose |
|-------------|-----------|---------|
| Mistral AI / OpenAI | User-generated content (chat messages) | AI chat functionality |

**Note:** Chat messages are shared transiently with AI providers for response generation. Messages are not stored by Blips servers.

### Security Practices

| Practice | Status |
|----------|--------|
| Data encrypted in transit | ✅ Yes (HTTPS/TLS) |
| Data encrypted at rest | ✅ Yes (database encryption) |
| Data deletion available | ✅ Yes (in-app deletion + support contact) |
| Security review | ✅ (internal) |

### Data Handling

| Question | Answer |
|----------|--------|
| Does the app collect data? | Yes |
| Is all collected data encrypted in transit? | Yes |
| Do you provide a way for users to request deletion? | Yes |
| Commitment to follow Play Families Policy? | N/A (not a kids app) |

---

## Privacy Policy Reference

Privacy policy URL: `https://tryblips.com/privacy.html`

The privacy policy covers:
- What data is collected and why
- How data is used for personalization
- Third-party services (Mistral AI, OpenAI, Sentry)
- Data retention periods (90 days interactions, 24h chat quotas)
- How to request data deletion
- Contact information

---

## Changelog

| Date | Change |
|------|--------|
| Feb 2026 | Initial documentation for App Store submissions |
