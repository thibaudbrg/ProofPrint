# Challenge - Fighting Identity Fraud in the Age of AI

The Swisscom challenge we took at the [Swiss {ai} Weeks Zürich Hackathon 2026](https://zh.ai-weeks.ch/).

> **How can we outsmart deepfakes to make digital onboarding and account recovery both fraud-resistant and frictionless?**

## Problem

Identity theft and digital fraud are rapidly increasing. Digital onboarding for Qualified Electronic Signatures (QeS) and other trust services is increasingly exposed to deepfakes, fake documents, injection attacks, and presentation attacks. AI-powered fraud is becoming more sophisticated, scalable, and inexpensive, undermining reliable digital identification and disproportionately targeting vulnerable groups, especially the elderly.

## Objective

Focus on **one specific attack vector** and:

1. Explain the attack vector and its impact.
2. Design a technical and UX-friendly mitigation.
3. Show how it was tested and validated, or define a test/validation concept if data is unavailable.
4. Explain how internal-data model training could improve the solution.

## Support for hackers

- Challenging and feedback from Trust Services and Validation experts from Swisscom's Digital Trust Unit.
- Access to Swisscom Trust Services: e-ID check, signatures, seals, and validator.

## Technical preferences

Select one concrete attack vector and develop a technical, UX-friendly mitigation with a validation concept. Potential directions include trust and behavioral signals, novel MFA, risk-based step-up authentication, deepfake / document / video / voice detection, phishing and social-engineering detection, fraud-data orchestration, agentic fraud operations, and cross-document checks. Swisscom MyAI or SwissAI Platform may be used, subject to confirmation.

## Why hack?

A strong digital economy depends on reliable online identity verification and secure onboarding for trusted digital transactions, signatures, and services. A hackathon enables rapid exploration of practical, AI-enabled countermeasures that improve security without adding unnecessary user friction.

## About the challenge partner

Swisscom is Switzerland's leading ICT company, providing secure and reliable connectivity, IT and digital services. Swisscom Digital Trust enables trusted digital business through secure identification and onboarding, legally compliant electronic signatures and seals, and scalable trust services for companies and institutions.

---

**Our attack vector:** deepfake video injected into the ID + selfie onboarding flow. See the [README](README.md) for what we built.
