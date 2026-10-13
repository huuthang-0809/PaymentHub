const path = require('path');
const CopyWebpackPlugin = require('copy-webpack-plugin');

module.exports = {
  entry: './src/module.tsx',
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: 'module.js',
    library: { type: 'system' },
    clean: true,
    uniqueName: 'paymenthub-evidence-panel',
  },
  externals: [/^@grafana\//, /^react$/, /^react-dom$/, /^react\/jsx-runtime$/],
  resolve: { extensions: ['.ts', '.tsx', '.js'] },
  module: {
    rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: {
      loader: 'ts-loader', options: { transpileOnly: true, compilerOptions: { noEmit: false } },
    } }],
  },
  plugins: [new CopyWebpackPlugin({ patterns: [{ from: 'src/plugin.json' }, { from: 'src/img', to: 'img' }] })],
};