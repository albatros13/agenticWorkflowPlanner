# Coding Agent Brief: BPMN-to-Reo Converter and SVG Editor

## 1. Project Goal

Develop a lightweight web application that converts a defined subset of BPMN into the Reo coordination language.

The project must not depend on legacy Reo Eclipse tooling or Eclipse-based editors.

The application should:

1. Import BPMN 2.0 XML.
2. Convert supported BPMN constructs into a structured Reo model.
3. Store and export the Reo model in a readable text-based format.
4. Render the Reo model as an SVG diagram in a browser.
5. Support future graphical model editing without replacing the parser, converter, model, or renderer.

Treat the mapping rules below as the initial supported subset. Preserve unsupported BPMN elements and report them clearly. Never silently invent a conversion.

## 2. Recommended Technology Stack

Use a small, modular TypeScript application.

Suggested stack:

- **Frontend:** React, TypeScript, Vite.
- **BPMN import:** A maintained XML parser with namespace-aware parsing.
- **Reo representation:** Typed, renderer-independent intermediate representation (IR).
- **Text format:** JSON initially, with a `.reo.json` extension.
- **SVG visualization:** React SVG components, optionally supported by D3.js.
- **Automatic graph layout:** ELK.js (`elkjs`) or another maintained graph layout engine.
- **Testing:** Vitest and Playwright.

Prefer JSON over a custom DSL for the initial implementation. JSON is easy to parse, validate, version, and exchange with other tools.

A custom Reo DSL may be introduced later if it provides clear benefits for human readability or formal analysis.

Keep the semantic model independent of the visualization library.

## 3. Architecture

Organize the project into independent modules:

```text
src/
  bpmn/
    parseBpmn.ts
    bpmnTypes.ts
    validateBpmn.ts

  conversion/
    convertBpmnToReo.ts
    mappingRules.ts
    diagnostics.ts
    resolveConnections.ts

  reo/
    reoTypes.ts
    reoSchema.ts
    serializeReo.ts
    parseReo.ts
    validateReo.ts

  layout/
    layoutGraph.ts

  render/
    ReoSvg.tsx
    symbols/
      Replicate.tsx
      Route.tsx
      Join.tsx
      FIFO1.tsx
      Filter.tsx
      Sync.tsx

  editor/
    ReoEditorPage.tsx

  examples/
    bpmn/

tests/
  conversion/
  reo/
  integration/
  fixtures/
```

The converter must not depend on React, SVG, D3.js, or browser APIs.

The SVG renderer must not contain BPMN conversion logic.

The Reo IR must be the single source of truth for both the textual representation and the graphical visualization.

## 4. Reo Intermediate Representation

Define a typed Reo model with stable IDs, textual annotations to preserve process semantics (task names, descriptions), and explicit endpoint references.

The initial representation must support:

### Nodes

- `Replicate`
- `Route`
- `Join`
- `