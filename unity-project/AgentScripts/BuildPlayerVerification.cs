using System;
using UnityEditor;
using UnityEditor.Build.Reporting;
public static class BuildPlayerVerification
{
    public static string Run()
    {
        Subsurface.Borehole.Editor.BoreholeBuildAssets.Ensure();
        var report=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{"Assets/Explorer/DocumentExplorer.unity"},locationPathName="Temp/PlayerVerification/Visualizer.exe",target=BuildTarget.StandaloneWindows64,options=BuildOptions.Development});
        if(report.summary.result!=BuildResult.Succeeded)throw new Exception("Player build failed: "+report.summary.result);
        return report.summary.result+" · "+report.summary.totalSize+" bytes";
    }
}
