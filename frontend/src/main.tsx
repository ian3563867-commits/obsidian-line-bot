import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/globals.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Missing #root element");
}

const apiUrl = rootEl.dataset.api || "";

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <App apiUrl={apiUrl} />
  </React.StrictMode>,
);
