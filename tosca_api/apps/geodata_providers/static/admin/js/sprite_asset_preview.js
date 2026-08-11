(function () {
  "use strict";

  const MAX_PREVIEW_ICONS = 500;

  function parseIndex(raw) {
    if (!raw || !raw.trim()) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("The sprite index must be a JSON object.");
    }
    return parsed;
  }

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderGallery(container, imageUrl, image, index) {
    const entries = Object.entries(index).sort(([left], [right]) =>
      left.localeCompare(right)
    );
    if (!entries.length) {
      container.appendChild(
        element("p", "sprite-preview-message", "The JSON index contains no images.")
      );
      return;
    }

    const gallery = element("div", "sprite-preview-gallery");
    for (const [name, metadata] of entries.slice(0, MAX_PREVIEW_ICONS)) {
      if (!metadata || typeof metadata !== "object") continue;
      const { x, y, width, height, pixelRatio } = metadata;
      if (
        ![x, y, width, height, pixelRatio].every(Number.isFinite) ||
        width <= 0 ||
        height <= 0 ||
        pixelRatio <= 0
      ) {
        continue;
      }

      const card = element("div", "sprite-preview-card");
      const viewport = element("div", "sprite-preview-icon-viewport");
      const icon = element("span", "sprite-preview-icon");
      icon.style.width = `${width / pixelRatio}px`;
      icon.style.height = `${height / pixelRatio}px`;
      icon.style.backgroundImage = `url("${imageUrl.replaceAll('"', '\\"')}")`;
      icon.style.backgroundPosition = `${-x / pixelRatio}px ${-y / pixelRatio}px`;
      icon.style.backgroundSize = `${image.naturalWidth / pixelRatio}px ${
        image.naturalHeight / pixelRatio
      }px`;
      viewport.appendChild(icon);
      card.appendChild(viewport);
      card.appendChild(element("strong", "sprite-preview-name", name));
      card.appendChild(
        element(
          "span",
          "sprite-preview-meta",
          `${width / pixelRatio}×${height / pixelRatio} CSS px · @${pixelRatio}x`
        )
      );
      gallery.appendChild(card);
    }
    container.appendChild(gallery);

    if (entries.length > MAX_PREVIEW_ICONS) {
      container.appendChild(
        element(
          "p",
          "sprite-preview-message",
          `Showing the first ${MAX_PREVIEW_ICONS} of ${entries.length} images.`
        )
      );
    }
  }

  function setupVariant(root, config) {
    const panel = element("section", "sprite-preview-panel");
    root.appendChild(panel);

    const imageInput = document.getElementById(config.imageInputId);
    const clearInput = document.getElementById(`${config.imageInputId}-clear`);
    const indexInput = document.getElementById(config.indexInputId);
    const indexFileInput = document.getElementById(config.indexFileInputId);
    let objectUrl = null;
    let objectUrlFile = null;

    function selectedImageUrl() {
      if (clearInput && clearInput.checked) return "";
      const file = imageInput && imageInput.files && imageInput.files[0];
      if (!file) return config.savedImageUrl;
      if (file !== objectUrlFile) {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = URL.createObjectURL(file);
        objectUrlFile = file;
      }
      return objectUrl;
    }

    function render() {
      panel.replaceChildren();
      panel.appendChild(element("h3", "sprite-preview-title", config.title));

      const imageUrl = selectedImageUrl();
      if (!imageUrl) {
        panel.appendChild(
          element("p", "sprite-preview-message", `No ${config.title} PNG selected.`)
        );
        return;
      }

      let index;
      try {
        index = parseIndex(indexInput ? indexInput.value : config.savedIndex);
      } catch (error) {
        panel.appendChild(element("p", "sprite-preview-error", error.message));
        return;
      }

      const sheetFrame = element("div", "sprite-preview-sheet-frame");
      const sheet = element("img", "sprite-preview-sheet");
      sheet.alt = `${config.title} sprite sheet`;
      sheet.src = imageUrl;
      sheetFrame.appendChild(sheet);
      panel.appendChild(sheetFrame);

      const galleryContainer = element("div", "sprite-preview-gallery-container");
      panel.appendChild(galleryContainer);
      sheet.addEventListener("load", function () {
        galleryContainer.replaceChildren();
        renderGallery(galleryContainer, imageUrl, sheet, index);
      });
      sheet.addEventListener("error", function () {
        galleryContainer.replaceChildren(
          element("p", "sprite-preview-error", "The selected PNG could not be loaded.")
        );
      });
    }

    if (imageInput) imageInput.addEventListener("change", render);
    if (clearInput) clearInput.addEventListener("change", render);
    if (indexInput) indexInput.addEventListener("input", render);
    if (indexFileInput) {
      indexFileInput.addEventListener("change", async function () {
        const file = indexFileInput.files && indexFileInput.files[0];
        if (!file) {
          render();
          return;
        }
        try {
          const parsed = parseIndex(await file.text());
          if (indexInput) indexInput.value = JSON.stringify(parsed, null, 2);
          render();
        } catch (error) {
          panel.replaceChildren(
            element("h3", "sprite-preview-title", config.title),
            element("p", "sprite-preview-error", `Invalid JSON: ${error.message}`)
          );
        }
      });
    }

    render();
  }

  function setupPreview(root) {
    root.replaceChildren();
    setupVariant(root, {
      title: "1x",
      imageInputId: "id_image",
      indexInputId: "id_index_content",
      indexFileInputId: "id_index_file",
      savedImageUrl: root.dataset.imageUrl || "",
      savedIndex: root.dataset.index || "{}",
    });
    setupVariant(root, {
      title: "@2x",
      imageInputId: "id_image_2x",
      indexInputId: "id_index_content_2x",
      indexFileInputId: "id_index_file_2x",
      savedImageUrl: root.dataset.imageUrl2x || "",
      savedIndex: root.dataset.index2x || "{}",
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-sprite-preview]").forEach(setupPreview);
  });
})();
