
import React from "react";
import { Link } from "react-router-dom";

const Index = () => {
  return (
    <div className="min-h-screen flex flex-col bg-gradient-to-b from-slate-900 to-slate-800">
      {/* Header */}
      <header className="py-6 px-4 sm:px-6 lg:px-8 border-b border-slate-700">
        <div className="max-w-7xl mx-auto flex justify-between items-center">
          <div className="flex items-center">
            <div className="text-3xl font-bold text-white">blips</div>
            <div className="ml-2 bg-cyan-500 text-xs text-white px-2 py-1 rounded-full">AI News</div>
          </div>
        </div>
      </header>

      {/* Hero Section with App Screenshots */}
      <section className="py-16 px-4 sm:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto text-center">
          <h1 className="text-4xl sm:text-5xl font-bold text-white mb-4">
            Your AI-Powered Tech News Feed
          </h1>
          <p className="text-xl text-slate-300 mb-12 max-w-2xl mx-auto">
            Stay informed with AI-curated articles, videos, and reels from the best tech sources.
          </p>
          
          {/* App Screenshots */}
          <div className="flex justify-center items-end gap-4 sm:gap-8 mb-12">
            <div className="transform -rotate-6 shadow-2xl rounded-3xl overflow-hidden w-48 sm:w-56 hidden sm:block">
              <img 
                src="/screenshots/articles.png" 
                alt="Blips Articles Feed" 
                className="w-full h-auto"
              />
            </div>
            <div className="transform scale-110 shadow-2xl rounded-3xl overflow-hidden w-56 sm:w-64 z-10 border-4 border-cyan-500/30">
              <img 
                src="/screenshots/videos.png" 
                alt="Blips Videos Feed" 
                className="w-full h-auto"
              />
            </div>
            <div className="transform rotate-6 shadow-2xl rounded-3xl overflow-hidden w-48 sm:w-56 hidden sm:block">
              <img 
                src="/screenshots/reels.png" 
                alt="Blips Reels" 
                className="w-full h-auto"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Main Content */}
      <main className="flex-grow">
        <div className="max-w-7xl mx-auto py-12 px-4 sm:px-6 lg:px-8">
          {/* Features */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
            <div className="bg-slate-800/50 backdrop-blur rounded-xl p-6 border border-slate-700">
              <div className="text-3xl mb-4">📰</div>
              <h3 className="text-lg font-semibold text-white mb-2">AI-Summarized Articles</h3>
              <p className="text-slate-400 text-sm">Get concise summaries of tech news from top sources, powered by GPT.</p>
            </div>
            <div className="bg-slate-800/50 backdrop-blur rounded-xl p-6 border border-slate-700">
              <div className="text-3xl mb-4">🎬</div>
              <h3 className="text-lg font-semibold text-white mb-2">Tech Videos</h3>
              <p className="text-slate-400 text-sm">Watch curated tech videos from YouTube channels you trust.</p>
            </div>
            <div className="bg-slate-800/50 backdrop-blur rounded-xl p-6 border border-slate-700">
              <div className="text-3xl mb-4">⚡</div>
              <h3 className="text-lg font-semibold text-white mb-2">Quick Reels</h3>
              <p className="text-slate-400 text-sm">Swipe through short-form tech content in a TikTok-style feed.</p>
            </div>
          </div>

          {/* API Section */}
          <div className="bg-slate-800 border border-slate-700 shadow-xl rounded-xl overflow-hidden">
            <div className="p-8">
              <h2 className="text-2xl font-bold text-white mb-6">Backend API</h2>
              
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                
                {/* API Status */}
                <div className="col-span-1 bg-slate-900 rounded-lg p-6 border border-slate-700">
                  <h3 className="text-xl font-semibold text-white mb-4">API Status</h3>
                  <div className="flex items-center space-x-2">
                    <div className="h-3 w-3 bg-green-500 rounded-full animate-pulse"></div>
                    <span className="text-green-400 font-medium">Running</span>
                  </div>
                  <div className="mt-4 text-sm text-slate-400">
                    <p>Docs: <code className="bg-slate-800 px-2 py-1 rounded">/docs</code></p>
                  </div>
                </div>
                
                {/* Key Endpoints */}
                <div className="col-span-2 bg-slate-900 rounded-lg p-6 border border-slate-700">
                  <h3 className="text-xl font-semibold text-white mb-4">Key Endpoints</h3>
                  <div className="space-y-3 text-sm">
                    <div className="flex">
                      <span className="bg-blue-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">GET</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/articles/next</span>
                    </div>
                    <div className="flex">
                      <span className="bg-blue-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">GET</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/videos/next</span>
                    </div>
                    <div className="flex">
                      <span className="bg-blue-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">GET</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/reels/next</span>
                    </div>
                    <div className="flex">
                      <span className="bg-green-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">POST</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/ai/respond</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="bg-slate-900 border-t border-slate-800">
        <div className="max-w-7xl mx-auto py-6 px-4 text-center text-slate-500 text-sm">
          <p>© 2026 Blips | FastAPI + PostgreSQL + Redis + OpenAI</p>
        </div>
      </footer>
    </div>
  );
};

export default Index;
