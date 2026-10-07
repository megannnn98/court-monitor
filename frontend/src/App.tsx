import { Route, Routes } from "react-router-dom";

export function App() {
  return (
    <Routes>
      <Route
        path="*"
        element={
          <main>
            <h1>court-monitor</h1>
            <p>React-оболочка подготовлена; операторские страницы пока остаются в legacy UI.</p>
          </main>
        }
      />
    </Routes>
  );
}
