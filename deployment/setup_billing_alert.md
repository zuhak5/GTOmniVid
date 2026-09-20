# Google Cloud Platform $0.01 Billing Alert Setup Guide

## Objective
Configure an automated Google Cloud billing budget and alert to guarantee that **GTOmniVid** operates strictly at **$0.00 / month**. If any unexpected resource allocation occurs, an instant email alert is dispatched before meaningful charges accrue.

---

## Step-by-Step Configuration in Google Cloud Console

### Step 1: Navigate to Budgets & Alerts
1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. In the top-left navigation menu (☰), go to **Billing**.
3. Select your Cloud Billing account.
4. In the left navigation sidebar under *Cost Management*, click **Budgets & alerts**.

### Step 2: Create a New Budget
1. Click **+ Create Budget** at the top of the page.
2. In the **Scope** section:
   * **Name:** `gtomnivid-zero-cost-alert`
   * **Projects:** Select the GCP project running your GTOmniVid instance.
   * **Services:** Select *All services*.
   * Click **Next**.

### Step 3: Define the Target Amount
1. In the **Amount** section:
   * **Budget type:** Select **Specified amount**.
   * **Target amount:** Enter **`$1.00`** (or `$0.01` if your currency supports it).
   * Click **Next**.

### Step 4: Set Alert Threshold Rules
1. In the **Actions** section, set the following trigger thresholds based on actual spend:
   * **Threshold 1:** `50%` ($0.50 actual spend)
   * **Threshold 2:** `90%` ($0.90 actual spend)
   * **Threshold 3:** `100%` ($1.00 actual spend)
2. Ensure **Email alerts to billing account administrators and users** is checked.
3. (Optional) Configure Cloud Monitoring notifications / Webhook if you want Telegram alert notifications.
4. Click **Finish**.

---

## Summary of GCP Always-Free Envelope Verification

Ensure your Compute Engine instance strictly adheres to these parameters:

| Resource | Setting Required | Why |
| :--- | :--- | :--- |
| **Instance Type** | `e2-micro` | 1 instance free per month in `us-east1`, `us-central1`, or `us-west1`. |
| **Boot Disk Type** | **Standard Persistent Disk (`pd-standard`)** | **Never select Balanced or SSD**. Balanced/SSD triggers immediate billing. |
| **Boot Disk Size** | Exactly **30 GB** | Up to 30 GB standard PD is 100% free. |
| **Network Service Tier** | **Standard Tier** | Premium Tier egress incurs higher rates. |
| **IP Address** | **Ephemeral IPv4** | Reserved unattached static IPs incur hourly charges. |
| **Outbound Egress** | <= 900 MB / month | Software-guarded by GTOmniVid `QuotaLedger` hard cap. |
