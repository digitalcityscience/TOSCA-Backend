# Safe updates and deletion

[User-guide home](README.md) · [Publishing](publishing.md) · [Troubleshooting](troubleshooting.md)

Many relationships use cascading deletion. Always read the Django confirmation page and dependency counts before confirming.

## Preferred alternatives to deletion

| Object | Safer alternative |
|---|---|
| Campaign | Set Private or Archived. |
| GeoStory | Set Draft or Archived. |
| Event | Set Draft or Cancelled. |
| Event Type/taxonomy term | Set inactive so existing records remain understandable. |
| Layer Style Assignment | Set inactive or change Default to Alternate. |
| Layer | Disable Public or unpublish through the provider workflow. |
| Footer | Publish a replacement Footer. |

## What deletion affects

- Deleting a **Campaign** permanently deletes its Events, Event Series, GeoStories, and feedback.
- Deleting a **Layer** removes its relationships from GeoStories, Events, and feedback. Check the usage summary first.
- Deleting a **Store** or **Geodata Engine** can affect many dependent provider objects and may require an explicit force-confirmation workflow.
- Deleting a **Style** may also require remote provider deletion; a remote failure can block the local deletion.
- Removing a GeoStory Layer or Event Layer inline removes only that relationship, not the underlying Layer.
- A currently published Footer cannot be deleted; publish another one first.

## Safe-change procedure

1. Record the object and its current public behavior.
2. Check dependency/usage counts and related objects.
3. Prefer a reversible state change.
4. If deletion is necessary, export or record any content required for audit/recovery according to project policy.
5. Confirm the target by name and scope on the final confirmation page.
6. After the change, test at least one affected public and authenticated workflow.

There is no Campaign recycle bin or soft-delete recovery. Treat Campaign deletion as permanent.

