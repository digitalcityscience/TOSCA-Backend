(function () {
  "use strict";

  const ORDERED_FORMSETS = ["logos", "documents"];

  document.addEventListener("formset:added", function (event) {
    const formsetName = event.detail?.formsetName;
    if (!ORDERED_FORMSETS.includes(formsetName)) {
      return;
    }

    const selector = `input[name^="${formsetName}-"][name$="-display_order"]`;
    const addedOrder = event.target.querySelector(selector);
    if (!addedOrder) {
      return;
    }

    const existingOrders = Array.from(document.querySelectorAll(selector))
      .filter(function (input) {
        if (input === addedOrder || input.name.includes("__prefix__")) {
          return false;
        }
        // Tabular inlines (logos) use table rows; stacked inlines use .inline-related.
        const row = input.closest("tr, .inline-related");
        const deleteInput = row?.querySelector('input[name$="-DELETE"]');
        return !deleteInput?.checked;
      })
      .map(function (input) {
        return Number.parseInt(input.value, 10);
      })
      .filter(Number.isFinite);

    addedOrder.value = String(
      existingOrders.length ? Math.max(...existingOrders) + 1 : 0,
    );
  });
})();
