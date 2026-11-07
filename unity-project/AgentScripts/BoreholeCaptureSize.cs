using System;
using System.Linq;
using System.Reflection;
using UnityEditor;

public static class BoreholeCaptureSize
{
    const BindingFlags Flags=BindingFlags.Instance|BindingFlags.Static|BindingFlags.Public|BindingFlags.NonPublic;
    static Assembly Assembly=>AppDomain.CurrentDomain.GetAssemblies().First(a=>a.GetType("UnityEditor.GameViewSizes")!=null);
    static object Group()
    {
        var type=Assembly.GetType("UnityEditor.GameViewSizes");var instance=typeof(ScriptableSingleton<>).MakeGenericType(type).GetProperty("instance").GetValue(null);
        return type.GetProperty("currentGroup",Flags).GetValue(instance);
    }
    public static string Setup()
    {
        var group=Group();var gt=group.GetType();var view=EditorWindow.GetWindow(Assembly.GetType("UnityEditor.GameView"));var index=view.GetType().GetProperty("selectedSizeIndex",Flags);
        if(!SessionState.GetBool("BoreholeQAResolutionActive",false))
        {
            int custom=(int)gt.GetMethod("GetCustomCount",Flags).Invoke(group,null);SessionState.SetInt("BoreholeQAOriginalSize",(int)index.GetValue(view));SessionState.SetInt("BoreholeQACustomSize",custom);
            var sizeType=Assembly.GetType("UnityEditor.GameViewSizeType");var fixedResolution=Enum.Parse(sizeType,"FixedResolution");var size=Activator.CreateInstance(Assembly.GetType("UnityEditor.GameViewSize"),fixedResolution,1440,900,"Borehole visual verification");
            gt.GetMethod("AddCustomSize",Flags).Invoke(group,new[]{size});SessionState.SetBool("BoreholeQAResolutionActive",true);
        }
        int builtIn=(int)gt.GetMethod("GetBuiltinCount",Flags).Invoke(group,null);index.SetValue(view,builtIn+SessionState.GetInt("BoreholeQACustomSize",0));view.Repaint();return "Game-view render resolution set to 1440x900 for visual QA";
    }
    public static string Restore()
    {
        var view=EditorWindow.GetWindow(Assembly.GetType("UnityEditor.GameView"));
        if(SessionState.GetBool("BoreholeQAResolutionActive",false))
        {
            var group=Group();view.GetType().GetProperty("selectedSizeIndex",Flags).SetValue(view,SessionState.GetInt("BoreholeQAOriginalSize",0));group.GetType().GetMethod("RemoveCustomSize",Flags).Invoke(group,new object[]{(int)group.GetType().GetMethod("GetBuiltinCount",Flags).Invoke(group,null)+SessionState.GetInt("BoreholeQACustomSize",0)});SessionState.SetBool("BoreholeQAResolutionActive",false);
        }
        view.maximized=SessionState.GetBool("BoreholeCaptureWasMaximized",false);view.Repaint();return "Original Game-view resolution and layout restored";
    }
}
