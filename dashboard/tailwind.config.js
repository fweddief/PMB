/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          500: "#4F46E5",
          600: "#4338CA",
          700: "#3730A3"
        }
      }
    },
  },
  plugins: [],
};
