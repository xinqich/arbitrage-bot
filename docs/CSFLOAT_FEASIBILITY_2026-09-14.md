# CSFloat feasibility check — 14 September 2026

The withdrawal destination is now CSFloat. DMarket remains a possible place to earn balance, which must ultimately be moved through genuine item purchases and sales. This note records the requested ballpark check; it does not change the running program, frozen paper prediction, or account settings.

## Result

No positive direct round trip was found in the tested cases using current Steam buy orders, current Steam listings, and CSFloat's minimum listing prices. Earning on DMarket and later selling transfer items on CSFloat still produces a positive numerical example. That example is not a verified profitable route: future prices, CSFloat buyers, and account-specific eligibility remain unproven.

CSFloat is not a guaranteed way to avoid identity checks. All payouts use Stripe; onboarding requires personal details and Stripe may request identity documents. CSFloat also has its own KYC process for some account/payment situations. Avoiding DMarket KYC and avoiding every provider's KYC are different requirements. See [payout onboarding](https://blog.csfloat.com/payment-changes/) and [CSFloat account help](https://csfloat.com/faq#account/know-your-customer).

## What was checked

- One anonymous CSFloat public price-index response, retrieved on 14 September 2026: https://csfloat.com/api/v1/listings/price-list . Values are minimum listing prices in USD cents and listed quantities, not completed sales, buyer demand, or quantity at the minimum price. The feed did not provide individual price-update timestamps.
- Steam public USD pages for 33 case names costing at most $10 on that index. 32 parsed successfully; Winter Offensive Weapon Case was excluded after the existing parser rejected the page. Successful observations were collected around 15:32–15:37 UTC. Exact times and source fields are in the archived research database.
- DMarket eligible sale listings and attribute-free buy orders for Fracture, Recoil, Revolution, and Kilowatt. A second Fracture listing request confirmed 100 eligible offers at $0.52, sufficient for the example's 19 initial and 39 later purchases at this snapshot. This does not reserve them for future dates.
- 8,159 whole-item combinations for CSFloat -> Steam -> CSFloat; 1,157 combinations starting from the four DMarket items and ending through any of the 32 CSFloat cases. These are alternative scenarios, not simultaneous purchases.
- Steam selling proceeds use the existing audited per-item fee calculation and consume successive bid levels when the best level has insufficient quantity. Steam purchases consume ask levels. CSFloat purchase quantity at its minimum price is an optimistic assumption, because the summary feed has no price-level depth.
- The screen sells immediately into Steam buy orders and assumes a later CSFloat sale at today's minimum asking price. It does not test patient Steam listings, negotiated discounts, sniping, skins, stickers, or future price increases. There is no claim that all possible arbitrage is impossible.

## Fees and payout limits

CSFloat's current [seller help](https://csfloat.com/faq#seller/fees) specifies 2% per listing, rounded UP to a whole USD cent. A $0.14 case therefore nets $0.13, and a $0.43 case nets $0.42. Its [withdrawal help](https://csfloat.com/faq#withdrawals) specifies 2.5% for the initial sales-volume tier, generally a $5 minimum payout with country-specific exceptions, and possible extra conversion/bank charges. Exact account payout availability is not verified.

The fresh DMarket fee response gives a 10% default, minimum one cent. This check uses that conservative default. Its returned discounted-item page does not establish every title's individual fee. No DMarket cash-withdrawal fee is used because the fallback moves value through CS2 items.

The main comparison applies exact Steam fees, CSFloat's cent-rounded sale fee, and 2.5% payout deduction. The raw analysis also retains a deliberately optimistic comparison without CSFloat sale-fee rounding: even that comparison found zero positive direct routes, including before the final payout fee. Deposit, currency-conversion, bank, and other account-specific costs are not included; they can worsen the result. Unspent Steam Wallet is reported separately and is never counted as withdrawable money.

## Current prices explaining the difference

| Case | Steam lowest listing | CSFloat lowest listing | DMarket highest buy order |
|---|---:|---:|---:|
| Fracture | $0.71 | $0.43 | $0.49, 4 items |
| Recoil | $0.41 | $0.26 | $0.40, 1 item |
| Revolution | $0.30 | $0.21 | $0.49, 1 item |
| Kilowatt | $0.19 | $0.14 | $0.41, 1 item; next $0.40, 96 items |

These columns have different meanings: a seller's asking price is not a buyer's commitment. In particular, CSFloat listed quantity does not establish sale speed. Authenticated CSFloat listings/buy orders and completed-sale evidence were unavailable: the filtered API returned HTTP 403, and the normal browser search explicitly required login. The public index worked without login. No access restriction was bypassed.

## Examples using most of the $10 budget

1. **CSFloat -> Steam -> CSFloat:** hypothetically buy 17 Fever Cases at $0.55 = $9.35. Selling through the observed Steam bids would yield $12.70 Wallet after Steam fees. Buy 66 Kilowatt Cases at $0.19 = $12.54, leaving $0.16 Wallet. Selling them at $0.14 on CSFloat would yield $8.58 after cent-rounded sale fees, about $8.37 after the payout percentage. That is about $0.98 below the $9.35 deployed, with $0.65 initial balance unused. CSFloat purchase depth and future sales are not established.
2. **DMarket -> Steam -> CSFloat:** buy 19 Fracture Cases at $0.52 = $9.88. Observed Steam bids yield $11.01 Wallet: 6 sales at $0.69 net $0.60 each, then 13 at $0.64 net $0.57 each. Buy 57 Kilowatt Cases for $10.83, leaving $0.18 Wallet. CSFloat sales at $0.14 net $7.41 balance, about $7.22 after payout fees. $0.12 stays on DMarket. This loses about $2.66 on the deployed $9.88.

All displayed post-payout values are approximate; the exact payout rounding and extra charges depend on the account and method.

## DMarket profit followed by an item transfer to CSFloat

At the same price snapshot, the second example changes substantially if the 57 Kilowatt Cases are sold on DMarket instead:

1. Start with $10 DMarket; spend $9.88 on 19 Fracture Cases, leaving $0.12.
2. Sell on Steam after eligibility for $11.01 net Wallet.
3. Spend $10.83 on 57 Kilowatt Cases, leaving $0.18 Wallet.
4. After eligibility, sell 1 Kilowatt at the observed $0.41 bid and 56 at $0.40. Applying the 10% default with upward cent rounding gives $0.36 each: $20.52 DMarket proceeds. Add the unused $0.12: $20.64.
5. Buy 39 Fracture Cases at $0.52 = $20.28, leaving $0.36 DMarket.
6. Withdraw those items to Steam, wait for eligibility, and sell them on CSFloat at the observed $0.43 listing level. At $0.42 net each, that gives $16.38 CSFloat balance, or approximately **$15.97 after the 2.5% payout deduction**, before conversion/bank costs.

That is an illustrative $5.97 cash increase over the original $10, plus separately retained $0.36 DMarket and $0.18 Steam Wallet. It is NOT a forecast or a paper outcome. The 39 CSFloat buyers, their prices, and the future DMarket/Steam books are not secured. CSFloat's existing competing listings can delay or prevent this exit. The transfer itself loses roughly 21% of the DMarket amount spent, so it must always be included when deciding whether the earlier profit is worthwhile.

## Timing

Two fixed 17-day cycles are not a reliable timing rule. The final transfer can be DMarket purchase -> Steam inventory -> CSFloat sale without another Steam Market sale and repurchase.

Using the existing 17-day allowance for the first DMarket round trip, then 7–10 days for the transfer item's eligibility/visibility, then about 8 days for CSFloat to release sale proceeds gives a conditional **32–35 days**, plus finding buyers, trade delivery, payout processing, and any extra account/item restrictions. CSFloat's precise ability to list a Steam-market-bought hidden item before day 10 has not been checked on this account, so the existing 10-day assumption must not be silently shortened.

DMarket says restricted Tradable proceeds can immediately fund CS2 market purchases, but not Target purchases or money withdrawals: [DMarket balance rules](https://support.dmarket.com/hc/en-us/articles/50280203883025-Tradable-balance-how-can-I-use-it). Actual ability to withdraw the newly purchased bridge items must still be confirmed. Its [visibility help](https://support.dmarket.com/hc/en-us/articles/25903434429841-Item-visibility-issue) states a 10-day visibility wait. CSFloat confirms roughly eight days between delivery and seller credit in its [current help](https://csfloat.com/faq#market/how-long-receive-proceeds).

## Recommendation

Keep DMarket as the candidate source of profit and investigate the complete route through to CSFloat payout. Do not implement a simple replacement of DMarket exit prices with CSFloat exit prices: this sample fails that comparison. Before committing money or describing the fallback as reliable, obtain CSFloat buy-order/recent-sale evidence and confirm the account's payout/KYC requirements and item eligibility. If the requirement is never providing identity information to any service, CSFloat does not establish that requirement.

Research only: no purchases, sales, fund transfers, account changes, program changes, or paper-route events were made. Evidence and standalone calculation scripts are archived separately from the bot's operational history in `data/research/csfloat-ballpark-20260914.zip`.
