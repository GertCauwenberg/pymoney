import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { keys } from "@pymoney/core/hooks";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { createWebClient } from "./api";
import { AppI18n } from "./i18n";
import "./styles.css";

const queryClient = createQueryClient();
// A 401 anywhere means the session is gone: forget the user and let the router
// send them to the login page. Nothing else needs to know.
const client = createWebClient(() => {
  queryClient.setQueryData(keys.session, null);
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ApiProvider client={client} queryClient={queryClient}>
      <AppI18n>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AppI18n>
    </ApiProvider>
  </StrictMode>,
);
