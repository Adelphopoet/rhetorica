import { loader } from "@monaco-editor/react";

// SQL/Python and AI diffs remain available on installations without public CDN access.
loader.config({ paths: { vs: "/vendor/monaco/vs" } });
