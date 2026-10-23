/**
 * Harness smoke: prove jest-expo + RNTL can render a React Native host.
 */
import { Text } from "react-native";
import { render, screen } from "@testing-library/react-native";

describe("jest-expo harness", () => {
  it("renders a React Native Text", () => {
    render(<Text testID="hello">Hello</Text>);
    expect(screen.getByTestId("hello")).toBeTruthy();
    expect(screen.getByText("Hello")).toBeTruthy();
  });
});
