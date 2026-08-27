import App from './App.svelte';
import { mount } from 'svelte';
import './app.css';

const target = document.getElementById('app');

if (!target) {
  throw new Error('Application root was not found.');
}

mount(App, { target });
