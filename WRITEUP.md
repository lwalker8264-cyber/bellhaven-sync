

**Matching approach.** I match on evidence, not names alone. I think this is important because as parent companies 
and their subsidiaries change so frequently names can easily confuse a user or even the database itself.
A website community is linked to a CRM account when the street address matches after normalizing abbreviations, 
or when the exact name matches in the same city. 
Phone numbers and whether the website's administrator is a CRM contact on the account raise confidence
and break ties between duplicates. This avoids the traps in the data: Amberly Manor exists in the CRM, but in
Colorado. "Carlisle" is not "New Carlisle." Union Square Senior Living is in the right city but at a different
address with a different administrator. Moves to a new parent follow the billing SOP. Tiffin and Marietta carry
revenue and open AR, so their old accounts are untouched and point to new Bellhaven accounts through
chow_current_account. Sandusky was sold to Millstone, which already had an account there, so I linked to it
rather than creating a third copy. Duplicates are marked Inactive with duplicate_of_account, since the API
has no merge or delete. When the data didn't support a confident call (a three-way tie in Kettering, a PO Box
billing address in Ashtabula, two Bellhaven accounts missing from the website), the account is set to
Needs Review with and a note is left to explain the circumstance behind flaggin it for review.

**How I used AI.** I built this with Claude: exploring the site and API, writing the code and tests. I made
the judgment calls, including which Kettering copy survives, keeping the PO Box as the billing address, and
flagging rather than guessing on missing communities. You'll see examples of all this in my video submission. 
I checked the output against the data. The test suite replays a snapshot of the sandbox and asserts the SOP 
outcomes, the traps, and that a second run proposes nothing new.

**What I'd build next.** A shared database for the decision log so the scheduled job and the reviewer use the
same queue. Alerts when a new community appears or one disappears. Phone and administrator updates (currently
used only as evidence). Fuzzy address matching for typos.
