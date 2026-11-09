(() => {
  "use strict";

  const sheet = '<path d="M6 2.75h8l4.75 4.75v13.75H6Z" fill="currentColor" fill-opacity=".09"/><path d="M14 2.75V7.5h4.75"/>';
  const icons = {
    file: { label: "文件", color: "#7a8793", art: sheet },
    folder: {
      label: "文件夹", color: "#b88228",
      art: '<path d="M2.5 6a2 2 0 0 1 2-2h5l2 2h8a2 2 0 0 1 2 2v11a1 1 0 0 1-1 1h-17a1 1 0 0 1-1-1Z" fill="currentColor" fill-opacity=".2"/><path d="M2.5 9h19"/>',
    },
    text: {
      label: "文本文件", color: "#64798b",
      art: sheet + '<path d="M9 11h6M9 14h6M9 17h4"/>',
    },
    markdown: {
      label: "Markdown 文档", color: "#547a9c",
      art: sheet + '<path d="M8.5 17v-5l2 2.5 2-2.5v5M15.5 12v5m-1.5-1.5 1.5 1.5 1.5-1.5"/>',
    },
    log: {
      label: "日志文件", color: "#75818b",
      art: sheet + '<path d="M11.5 11h4M11.5 14h4M11.5 17h4"/><path d="M9 11h.1M9 14h.1M9 17h.1" stroke-width="2.2"/>',
    },
    pdf: {
      label: "PDF 文档", color: "#bd5656",
      art: sheet + '<rect x="2.5" y="11" width="19" height="8" rx="1.5" fill="currentColor" stroke="none"/><text x="12" y="17" fill="white" stroke="none" text-anchor="middle" font-family="Arial, sans-serif" font-size="7" font-weight="700">PDF</text>',
    },
    word: {
      label: "文字文档", color: "#4e78b2",
      art: sheet + '<path d="m8.5 11.5 1.25 6 2.25-4.75 2.25 4.75 1.25-6" stroke-width="1.7"/>',
    },
    spreadsheet: {
      label: "表格文件", color: "#408267",
      art: sheet + '<rect x="8.5" y="10.5" width="8" height="8" rx=".5"/><path d="M8.5 13h8M8.5 15.75h8M11.5 10.5v8"/>',
    },
    presentation: {
      label: "演示文稿", color: "#b67245",
      art: sheet + '<path d="M12 10.5a4 4 0 1 0 4 4h-4Z" fill="currentColor" fill-opacity=".2"/><path d="M14 9.5v3h3a3 3 0 0 0-3-3Z" fill="currentColor" stroke="none"/>',
    },
    image: {
      label: "图片文件", color: "#8c68a5",
      art: '<rect x="3" y="3.5" width="18" height="17" rx="2" fill="currentColor" fill-opacity=".09"/><circle cx="8.25" cy="8.5" r="1.5"/><path d="m3 17 5-5 3.5 3.5 3.75-5 5.75 7"/>',
    },
    video: {
      label: "视频文件", color: "#a46883",
      art: '<rect x="3" y="3.5" width="18" height="17" rx="2" fill="currentColor" fill-opacity=".09"/><path d="M3 7.5h18M7 3.5v4M12 3.5v4M17 3.5v4"/><path d="m10 11 5 3-5 3Z" fill="currentColor" stroke="none"/>',
    },
    audio: {
      label: "音频文件", color: "#668f78",
      art: '<path d="M9 17V5l11-2v12M9 8l11-2"/><ellipse cx="6.25" cy="17.75" rx="2.75" ry="2.25" fill="currentColor" fill-opacity=".2"/><ellipse cx="17.25" cy="15.75" rx="2.75" ry="2.25" fill="currentColor" fill-opacity=".2"/>',
    },
    archive: {
      label: "压缩文件", color: "#a78646",
      art: sheet + '<path d="M11.5 3v9" stroke-dasharray="1 2" stroke-width="3"/><rect x="10" y="13" width="3" height="4.5" rx="1" fill="currentColor" fill-opacity=".2"/>',
    },
    code: {
      label: "代码文件", color: "#4d8a9b",
      art: sheet + '<path d="m10 11-3 3 3 3m5-6 3 3-3 3m-1.5-6-2 6"/>',
    },
    shell: {
      label: "脚本文件", color: "#567b84",
      art: '<rect x="2.5" y="4" width="19" height="16" rx="2" fill="currentColor" fill-opacity=".12"/><path d="m6 9 3 3-3 3m6 1h5"/>',
    },
    config: {
      label: "配置文件", color: "#827491",
      art: sheet + '<path d="M9 10v8m3-8v8m3-8v8"/><path d="M8 12h2m1 4h2m1-3h2" stroke-width="3"/>',
    },
    executable: {
      label: "程序文件", color: "#6d829b",
      art: '<rect x="2.5" y="3.5" width="19" height="17" rx="2" fill="currentColor" fill-opacity=".1"/><path d="M2.5 8h19M6 5.75h.1M9 5.75h.1"/><path d="m10 11.5 5 3-5 3Z" fill="currentColor" stroke="none"/>',
    },
  };

  const suffixes = new Map();
  const groups = {
    text: "txt text rtf rst adoc asc nfo",
    markdown: "md markdown mdx",
    log: "log",
    pdf: "pdf",
    word: "doc docx docm odt pages",
    spreadsheet: "xls xlsx xlsm xlsb ods csv tsv numbers",
    presentation: "ppt pptx pptm pps ppsx odp key",
    image: "jpg jpeg png gif bmp svg webp ico tif tiff avif heic heif psd eps raw",
    video: "mp4 mkv mov avi webm wmv flv m4v mpeg mpg ts mts m2ts",
    audio: "mp3 wav flac aac m4a ogg opus wma aiff aif mid midi",
    archive: "zip 7z rar tar gz bz bz2 xz zst zstd tgz tbz tbz2 txz tzst lz lzma cab iso",
    code: "py pyw ipynb js jsx mjs cjs ts tsx html htm css scss sass less c h cpp cc cxx hpp cs java go rs rb php swift kt kts scala lua r pl pm sql vue svelte dart ex exs erl hs f f90 jl m mm asm s spec",
    shell: "sh bash zsh fish ps1 psm1 psd1 bat cmd",
    config: "json jsonc json5 yaml yml toml ini conf cfg config properties env lock xml xsd xsl xslt plist reg service socket desktop gitignore gitattributes editorconfig",
    executable: "exe msi msix appx dll so dylib bin appimage jar apk deb rpm wasm",
  };
  for (const [type, extensions] of Object.entries(groups)) {
    for (const extension of extensions.split(" ")) suffixes.set(extension, type);
  }

  const namedFiles = new Map([
    ...["readme", "license", "licence", "copying", "notice", "changelog", "authors"].map((name) => [name, "text"]),
    ...["dockerfile", "containerfile", "makefile", "gnumakefile", "cmakelists.txt", "rakefile", "gemfile"].map((name) => [name, "code"]),
    ...[".bashrc", ".bash_profile", ".bash_logout", ".zshrc", ".zprofile", ".profile"].map((name) => [name, "shell"]),
    ...[".env", ".gitignore", ".gitattributes", ".gitmodules", ".editorconfig", ".npmrc", ".yarnrc", ".dockerignore", ".prettierrc", ".eslintrc", ".babelrc"].map((name) => [name, "config"]),
  ]);

  function describe(entry) {
    let type = "file";
    if (entry && entry.isDir) {
      type = "folder";
    } else {
      const name = String((entry && entry.name) || "").toLowerCase();
      const dot = name.lastIndexOf(".");
      type = namedFiles.get(name) || (dot > 0 && suffixes.get(name.slice(dot + 1))) || "file";
      if (name.startsWith(".env.")) type = "config";
      if (/^(dockerfile|containerfile)\./.test(name)) type = "code";
      if (/\.so(?:\.\d+)+$/.test(name)) type = "executable";
    }
    return { type, label: icons[type].label };
  }

  function create(entry) {
    const { type, label } = describe(entry);
    const icon = icons[type];
    const wrapper = document.createElement("span");
    wrapper.className = `file-icon file-icon--${type}`;
    wrapper.setAttribute("aria-hidden", "true");
    wrapper.title = label;
    wrapper.style.setProperty("--file-icon-color", icon.color);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.5");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("focusable", "false");
    // Only our static SVG catalog enters markup; remote filenames never do.
    svg.innerHTML = icon.art;
    wrapper.append(svg);
    return wrapper;
  }

  const api = { create, describe };
  if (typeof window !== "undefined") window.FileIcons = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
