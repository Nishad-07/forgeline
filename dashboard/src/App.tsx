import { Link, Route, Routes } from "react-router-dom";
import { Layout } from "./components/layout/Layout";
import { Button, Card, EmptyState } from "./components/ui/primitives";
import { ConsolePage } from "./pages/ConsolePage";
import { HistoryPage } from "./pages/HistoryPage";
import { PolicyPage } from "./pages/PolicyPage";
import { RunPage } from "./pages/RunPage";

function NotFound() {
  return (
    <div className="mx-auto max-w-xl pt-10">
      <Card>
        <EmptyState
          title="Page not found"
          
          action={
            <Link to="/">
              <Button variant="primary">Open console</Button>
            </Link>
          }
        />
      </Card>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ConsolePage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="runs/:id" element={<RunPage />} />
        <Route path="policy" element={<PolicyPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
