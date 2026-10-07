/*
 * Category item picker (external catalog ticket 03b).
 *
 * Two lists like a classic two-panel multiselect: everything the chosen
 * service offers (from the local source index, filtered by theme and text,
 * grouped by dataset) on the left, the category's items in display order
 * on the right. Moves only change a hidden JSON list of keys; saving the
 * category form applies them (item:<uuid> keeps an item, src:<id> adds one).
 *
 * Remote-derived text is only inserted with textContent / option text.
 */
(function () {
  "use strict";

  // Compact source rows from the sources endpoint.
  const ID = 0;
  const DATASET_ID = 1;
  const DATASET_TITLE = 2;
  const SOURCE_ID = 3;
  const TITLE = 4;
  const COUNT = 5;
  const THEMES = 6;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(text, title, onClick) {
    const node = el("button", "category-picker__button", text);
    node.type = "button";
    node.title = title;
    node.setAttribute("aria-label", title);
    node.addEventListener("click", onClick);
    return node;
  }

  function fetchJson(url, options) {
    return fetch(url, Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, options))
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (body) {
            if (!response.ok) throw new Error(body.error || "HTTP " + response.status);
            return body;
          });
      });
  }

  function csrfToken() {
    const input = document.querySelector("[name=csrfmiddlewaretoken]");
    if (input) return input.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function formatCount(count) {
    return count == null ? "" : " · " + count.toLocaleString();
  }

  function setup(root) {
    const config = JSON.parse(root.querySelector("#category-picker-config").textContent);
    const hidden = root.querySelector("[data-category-picker-value]");
    const services = config.services;
    const entries = config.entries.slice();
    const cache = new Map(); // service id -> sources payload
    let current = null; // payload of the shown service
    let rowsById = new Map();
    let visibleRows = [];

    // ---------------------------------------------------------------- layout
    const toolbar = el("div", "category-picker__toolbar");
    const serviceSelect = el("select", "category-picker__service");
    services.forEach(function (service) {
      const option = el("option", "", service.title);
      option.value = service.id;
      serviceSelect.appendChild(option);
    });
    const themeSelect = el("select", "category-picker__theme");
    const search = el("input", "category-picker__search");
    search.type = "search";
    search.placeholder = "Filter by title, dataset or id…";
    const mode = el("select", "category-picker__mode");
    [
      ["collections", "Show collections"],
      ["datasets", "Show datasets (move all collections)"],
    ].forEach(function (pair) {
      const option = el("option", "", pair[1]);
      option.value = pair[0];
      mode.appendChild(option);
    });
    toolbar.append(serviceSelect, themeSelect, search, mode);

    const catalogBar = el("div", "category-picker__catalog");
    const catalogText = el("span", "category-picker__note");
    const catalogButton = el("button", "category-picker__catalog-button");
    catalogButton.type = "button";
    catalogBar.append(catalogText, catalogButton);

    const panels = el("div", "category-picker__panels");
    const leftPanel = el("div", "category-picker__panel");
    const leftHead = el("div", "category-picker__head");
    const left = el("select", "category-picker__list");
    left.multiple = true;
    left.size = 18;
    left.setAttribute("aria-label", "Available sources");
    leftPanel.append(leftHead, left);

    const moves = el("div", "category-picker__moves");
    moves.append(
      button("»", "Add selected", addSelected),
      button("»»", "Add everything the filters show", addAllFiltered),
      button("«", "Remove selected", removeSelected),
      button("««", "Remove all", removeAll),
    );

    const rightPanel = el("div", "category-picker__panel");
    const rightHead = el("div", "category-picker__head");
    const right = el("select", "category-picker__list");
    right.multiple = true;
    right.size = 18;
    right.setAttribute("aria-label", "Items of this category");
    const order = el("div", "category-picker__order");
    const settingsLink = el("a", "category-picker__settings", "Map settings of the selected item");
    settingsLink.hidden = true;
    order.append(button("↑", "Move up", moveUp), button("↓", "Move down", moveDown), settingsLink);
    rightPanel.append(rightHead, right, order);

    panels.append(leftPanel, moves, rightPanel);
    const status = el("p", "category-picker__note");
    root.append(toolbar, catalogBar, panels, status);

    if (!services.length) {
      toolbar.hidden = true;
      catalogBar.hidden = true;
      leftHead.textContent = "No services: add an external service first.";
    }

    // ------------------------------------------------------------- helpers
    function serviceById(id) {
      return services.find(function (service) {
        return service.id === id;
      });
    }

    function matchOf(serviceId, row) {
      return serviceId + "|" + row[DATASET_ID] + "|" + row[SOURCE_ID];
    }

    function usedMatches() {
      return new Set(
        entries.map(function (entry) {
          return entry.match;
        }),
      );
    }

    function entryFor(row) {
      const service = serviceById(serviceSelect.value);
      return {
        key: "src:" + row[ID],
        match: matchOf(service.id, row),
        title: row[TITLE],
        datasetTitle: row[DATASET_TITLE],
        serviceTitle: service.title,
        settingsUrl: null,
      };
    }

    function save(message) {
      hidden.value = JSON.stringify(
        entries.map(function (entry) {
          return entry.key;
        }),
      );
      renderLeft();
      renderRight();
      status.textContent = message ? message + " Save the category to apply." : "";
    }

    // ----------------------------------------------------------- rendering
    function renderThemes() {
      const previous = themeSelect.value;
      themeSelect.replaceChildren();
      const all = el("option", "", "All themes");
      all.value = "";
      themeSelect.appendChild(all);
      (current ? current.themes : []).forEach(function (theme) {
        const option = el("option", "", theme.label + " (" + theme.datasets + " datasets)");
        option.value = theme.code;
        themeSelect.appendChild(option);
      });
      themeSelect.value = previous;
      if (themeSelect.value !== previous) themeSelect.value = "";
      themeSelect.hidden = !current || !current.themes.length;
    }

    function filteredRows() {
      if (!current) return [];
      const used = usedMatches();
      const query = search.value.trim().toLowerCase();
      const theme = themeSelect.value;
      const serviceId = serviceSelect.value;
      return current.sources.filter(function (row) {
        if (used.has(matchOf(serviceId, row))) return false;
        if (theme && row[THEMES].indexOf(theme) === -1) return false;
        if (!query) return true;
        return (row[TITLE] + " " + row[DATASET_TITLE] + " " + row[SOURCE_ID] + " " + row[DATASET_ID])
          .toLowerCase()
          .includes(query);
      });
    }

    function renderLeft() {
      visibleRows = filteredRows();
      const fragment = document.createDocumentFragment();
      if (mode.value === "datasets") {
        const datasets = new Map();
        visibleRows.forEach(function (row) {
          const key = row[DATASET_ID];
          if (!datasets.has(key)) datasets.set(key, { title: row[DATASET_TITLE], count: 0 });
          datasets.get(key).count += 1;
        });
        datasets.forEach(function (dataset, key) {
          const option = el("option", "", dataset.title + " (" + dataset.count + ")");
          option.value = "ds:" + key;
          fragment.appendChild(option);
        });
        leftHead.textContent = "Available: " + datasets.size + " datasets, " + visibleRows.length + " collections";
      } else {
        let group = null;
        let groupKey = null;
        visibleRows.forEach(function (row) {
          if (row[DATASET_ID] !== groupKey || group === null) {
            groupKey = row[DATASET_ID];
            group = document.createElement("optgroup");
            group.label = row[DATASET_TITLE] || row[DATASET_ID] || "—";
            fragment.appendChild(group);
          }
          const option = el("option", "", row[TITLE] + formatCount(row[COUNT]));
          option.value = "src:" + row[ID];
          option.title = row[DATASET_ID] ? row[DATASET_ID] + " / " + row[SOURCE_ID] : row[SOURCE_ID];
          group.appendChild(option);
        });
        const total = current ? current.sources.length : 0;
        leftHead.textContent = "Available: " + visibleRows.length + " of " + total;
      }
      left.replaceChildren(fragment);
    }

    function renderRight() {
      const selected = new Set(
        Array.from(right.selectedOptions).map(function (option) {
          return option.value;
        }),
      );
      const multipleServices =
        new Set(
          entries.map(function (entry) {
            return entry.serviceTitle;
          }),
        ).size > 1;
      const fragment = document.createDocumentFragment();
      entries.forEach(function (entry) {
        let text = entry.title;
        if (entry.datasetTitle && entry.datasetTitle !== entry.title) text += " — " + entry.datasetTitle;
        if (multipleServices) text += " [" + entry.serviceTitle + "]";
        if (!entry.settingsUrl) text = "+ " + text;
        const option = el("option", "", text);
        option.value = entry.key;
        option.selected = selected.has(entry.key);
        fragment.appendChild(option);
      });
      right.replaceChildren(fragment);
      rightHead.textContent = "In this category: " + entries.length + " (+ = added, not saved yet)";
      updateSettingsLink();
    }

    function updateSettingsLink() {
      const chosen = Array.from(right.selectedOptions);
      const entry =
        chosen.length === 1 &&
        entries.find(function (candidate) {
          return candidate.key === chosen[0].value;
        });
      settingsLink.hidden = !(entry && entry.settingsUrl);
      if (!settingsLink.hidden) settingsLink.href = entry.settingsUrl;
    }

    // --------------------------------------------------------------- moves
    function rowsForOption(value) {
      if (value.indexOf("ds:") === 0) {
        const datasetId = value.slice(3);
        return visibleRows.filter(function (row) {
          return row[DATASET_ID] === datasetId;
        });
      }
      const row = rowsById.get(Number(value.slice(4)));
      return row ? [row] : [];
    }

    function add(rows) {
      if (!rows.length) return;
      if (rows.length > config.confirmAbove && !window.confirm("Add " + rows.length + " items to this category?")) return;
      const used = usedMatches();
      let added = 0;
      rows.forEach(function (row) {
        const entry = entryFor(row);
        if (used.has(entry.match)) return;
        used.add(entry.match);
        entries.push(entry);
        added += 1;
      });
      save("Added " + added + ".");
    }

    function addSelected() {
      const rows = [];
      Array.from(left.selectedOptions).forEach(function (option) {
        rows.push.apply(rows, rowsForOption(option.value));
      });
      add(rows);
    }

    function addAllFiltered() {
      add(visibleRows.slice());
    }

    function remove(keys) {
      if (!keys.size) return;
      if (keys.size > config.confirmAbove && !window.confirm("Remove " + keys.size + " items from this category?")) return;
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        if (keys.has(entries[index].key)) entries.splice(index, 1);
      }
      save("Removed " + keys.size + ".");
    }

    function removeSelected() {
      remove(
        new Set(
          Array.from(right.selectedOptions).map(function (option) {
            return option.value;
          }),
        ),
      );
    }

    function removeAll() {
      remove(
        new Set(
          entries.map(function (entry) {
            return entry.key;
          }),
        ),
      );
    }

    function move(step) {
      const selected = new Set(
        Array.from(right.selectedOptions).map(function (option) {
          return option.value;
        }),
      );
      if (!selected.size) return;
      const indexes = entries
        .map(function (entry, index) {
          return selected.has(entry.key) ? index : -1;
        })
        .filter(function (index) {
          return index !== -1;
        });
      if (step < 0 ? indexes[0] === 0 : indexes[indexes.length - 1] === entries.length - 1) return;
      (step < 0 ? indexes : indexes.slice().reverse()).forEach(function (index) {
        const moved = entries[index];
        entries[index] = entries[index + step];
        entries[index + step] = moved;
      });
      save("Order changed.");
    }

    function moveUp() {
      move(-1);
    }

    function moveDown() {
      move(1);
    }

    // ------------------------------------------------------------- catalog
    let polling = false;

    function showCatalog(info) {
      catalogButton.hidden = false;
      catalogButton.disabled = false;
      if (info.state === "running") {
        catalogText.textContent = "Loading the catalog from the service… this can take a few minutes.";
        catalogButton.hidden = true;
        poll();
      } else if (info.state === "failed") {
        catalogText.textContent = "Loading the catalog failed: " + info.error;
        catalogButton.textContent = "Try again";
      } else if (info.state === "done") {
        catalogText.textContent =
          "Catalog loaded " + new Date(info.loaded_at).toLocaleString() + "." + (info.note ? " " + info.note : "");
        catalogButton.textContent = "Update catalog";
      } else {
        catalogText.textContent = "This service's catalog has not been loaded yet.";
        catalogButton.textContent = "Load catalog";
      }
    }

    function poll() {
      if (polling) return;
      polling = true;
      const service = serviceById(serviceSelect.value);
      (function next() {
        setTimeout(function () {
          if (!root.isConnected || serviceSelect.value !== service.id) {
            polling = false;
            return;
          }
          fetchJson(service.catalogUrl)
            .then(function (info) {
              if (info.state === "running") return next();
              polling = false;
              cache.delete(service.id);
              loadService();
            })
            .catch(function (error) {
              polling = false;
              catalogText.textContent = "Could not check the catalog load: " + error.message;
            });
        }, 3000);
      })();
    }

    catalogButton.addEventListener("click", function () {
      const service = serviceById(serviceSelect.value);
      catalogButton.disabled = true;
      fetchJson(service.catalogUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-CSRFToken": csrfToken() },
      })
        .then(showCatalog)
        .catch(function (error) {
          catalogButton.disabled = false;
          catalogText.textContent = "Could not start loading the catalog: " + error.message;
        });
    });

    // --------------------------------------------------------------- wiring
    function loadService() {
      const service = serviceById(serviceSelect.value);
      if (!service) return;
      current = null;
      renderThemes();
      renderLeft();
      leftHead.textContent = "Loading…";
      const cached = cache.get(service.id);
      const request = cached ? Promise.resolve(cached) : fetchJson(service.sourcesUrl);
      request
        .then(function (payload) {
          if (serviceSelect.value !== service.id) return;
          cache.set(service.id, payload);
          current = payload;
          rowsById = new Map(
            payload.sources.map(function (row) {
              return [row[ID], row];
            }),
          );
          renderThemes();
          renderLeft();
          showCatalog(payload.catalog);
        })
        .catch(function (error) {
          leftHead.textContent = "Could not load the sources: " + error.message;
        });
    }

    let searchTimer = null;
    search.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderLeft, 120);
    });
    themeSelect.addEventListener("change", renderLeft);
    mode.addEventListener("change", renderLeft);
    serviceSelect.addEventListener("change", loadService);
    left.addEventListener("dblclick", function (event) {
      if (event.target instanceof HTMLOptionElement) add(rowsForOption(event.target.value));
    });
    right.addEventListener("dblclick", function (event) {
      if (event.target instanceof HTMLOptionElement) remove(new Set([event.target.value]));
    });
    right.addEventListener("change", updateSettingsLink);

    renderRight();
    loadService();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-category-picker]").forEach(setup);
  });
})();
