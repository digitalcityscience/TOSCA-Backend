# How to create Events and Event Series

[User-guide home](README.md) · [Configure a Campaign](campaigns.md) · [Prepare map layers](geodata.md) · [Event taxonomy](event-taxonomy.md) · [Publishing rules](publishing.md)

Use an **Event** for one occurrence. Use an **Event Series** when occurrences share content or follow a recurrence rule.

## Create a single Event

Open **Events → Events → Add Event**.

| Field | Meaning and guidance |
|---|---|
| Campaign | Owning campaign and authoritative visibility boundary. |
| Event type | Optional category used for filtering/classification. |
| Title | Public event name. |
| Summary | Short listing text, up to 100 characters. |
| Content override | Occurrence-specific rich content. Leave null to inherit Series default content. An explicitly empty document suppresses inherited content. |
| Start datetime | Start date and time. |
| End datetime | End date and time; cannot be earlier than Start datetime. |
| Location mode | Physical, Online, Hybrid, By Arrangement, or Home Visit. See the rules below. |
| Location | WGS84 map point. Required for Physical and Hybrid; forbidden for Online, By Arrangement, and Home Visit. |
| Venue address | Human-readable physical venue. |
| District | Area used for display/filtering. |
| Online URL | Join or information URL. Online and Hybrid need this or Online platform. |
| Online platform | Name of the online service; can satisfy online-access validation when no URL is available. |
| Access notes | Accessibility, arrival, eligibility, or booking instructions. |
| Provider name/address/phone/email/social/URL | Public contact details for the organization providing the event. Publish only information intended for the public. |
| Language | One or more supported language codes. |
| Language note | Free text allowed only when `other` is included in Language. |
| Lead name | Named event lead/contact. |
| External URL | Canonical external detail or registration page. |
| Series | Optional parent Event Series. |
| Occurrence index | Position generated within a series; normally system-managed. |
| Is exception | Marks an occurrence changed from the recurrence template. |
| Original start datetime | Original scheduled time for a moved exception. |
| Organizer | Responsible user. |
| Status | Draft, Published, or Cancelled. |
| Visibility | Legacy display field. Authorization is determined by Campaign visibility. |

### Location-mode rules

- **Physical:** requires a map point.
- **Online:** requires an Online URL or Online platform and must not have a map point.
- **Hybrid:** requires both a map point and online access information.
- **By Arrangement / Home Visit:** must not expose a map point; put safe instructions in Access notes.

## Add map layers

In the **Event Layers** inline, choose a synchronized Published + Public layer and set Display order. A layer can appear only once per Event. Unlike GeoStories, an Event layer does not pin a style assignment; it uses the layer's configured/default styling. See [Prepare geodata, styles, and sprites](geodata.md).

## Create an Event Series

Open **Events → Event Series → Add Event Series**.

| Field | Meaning and guidance |
|---|---|
| Campaign | Campaign that owns the series and occurrences. |
| Event type | Optional shared event category. |
| Name | Internal/public identifying name; use a recognizable label. |
| Created by | Responsible creator; required and normally set automatically. |
| Default content | Rich content inherited by occurrences without an override. |
| Series mode | **Manual Batch** groups manually managed occurrences; **Recurring** generates by rule. |
| Recurrence type | Daily, Weekly, or Monthly. Required only for Recurring. |
| Start date | First occurrence date; required. |
| End date | Last recurrence boundary. For Recurring, use this or Occurrence count, never both. Not allowed for Manual Batch. |
| Occurrence count | Number of generated occurrences. Alternative to End date; at least 1. Not allowed for Manual Batch. |
| Interval | Repeat every N days/weeks/months; minimum 1. |
| Start time | Occurrence start time; required. |
| End time | Occurrence end time; required and later than Start time for same-day/recurring generation. |
| Timezone | IANA timezone for generation, for example `Europe/Berlin`; required. |
| By weekday | Weekday list for weekly recurrence; at least one is required for Weekly. |
| Monthly rule type | Day of Month or Nth Weekday; required for Monthly. |
| Day of month | Day 1–31 when using Day of Month. |
| Week of month | Week 1–5 when using Nth Weekday. |
| Weekday of month | Weekday used with Week of month. |
| Notes | Internal/editorial notes about the series. |

Manual Batch must not contain recurrence type, end date, occurrence count, weekday, or monthly-rule values. For Recurring, choose exactly one stopping condition: End date or Occurrence count.

## Publishing checks

- Confirm campaign visibility; Event visibility does not override it.
- Verify timezone and daylight-saving behavior for recurring dates.
- Confirm the end is after the start.
- Check location-mode requirements and avoid exposing private home locations.
- Test public URLs and public contact details.
- Verify referenced map layers load.
- Use Cancelled rather than deleting an occurrence users may already know about.

## Suggested screenshots

- `images/event-editor.png`: Event identity, schedule, status, and location mode.
- `images/event-location-contact.png`: location and provider/contact sections using test data.
- `images/event-series-recurrence.png`: recurring Series rule fields.
- `images/event-layers.png`: Event Layer inline.
