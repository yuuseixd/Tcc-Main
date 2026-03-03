import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders SentCrypto heading", () => {
  render(<App />);
  const heading = screen.getByText(/SentCrypto/i);
  expect(heading).toBeInTheDocument();
});
