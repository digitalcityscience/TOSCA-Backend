# Event types and taxonomy

[User-guide home](README.md) · [Events and Event Series](events.md) · [Permissions](permissions.md)

Event Types define which profile and taxonomy fields appear for an Event or Event Series. Taxonomy dimensions provide consistent categories used by filtering, search, and frontend presentation.

## Select taxonomy while editing an Event

1. Choose the Event Type first.
2. Wait for the relevant taxonomy dimension fields to appear.
3. Select the best matching term in each applicable dimension.
4. Do not repeat the same dimension through multiple assignments.
5. Save and reopen the Event to verify the selections.

Only active compatible dimensions are normally offered. An inactive dimension already assigned to existing content may remain visible so editors can understand and safely migrate old data.

## Event Type fields

| Field | Meaning |
|---|---|
| Code | Stable unique machine identifier. Do not change it casually after use. |
| Label | Human-readable name shown to editors and users. |
| Profile mode | **Core** uses only core behavior; **Extension** enables a named extension profile. |
| Profile key | Required for Extension and forbidden for Core. Identifies the extension profile. |
| Active | Whether the Event Type is offered for new selection. Deactivate instead of deleting a type still in use. |

Event Type and taxonomy registries are shared reference data. They should normally be maintained by platform administrators, not created ad hoc by content editors.

## Good classification practice

- Choose the narrowest accurate term, not the most visible term.
- Apply the same criteria across similar Events.
- Do not create spelling variants or duplicates to work around a missing term.
- Request registry changes through the responsible administrator.
- Test frontend filters after adding or changing shared taxonomy definitions.

