using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UIElements;
namespace Subsurface.Explorer
{
    public sealed partial class DocumentExplorer
    {
        [Serializable]class ServerHealth {public bool ok,worker_alive;public string db;}
        readonly List<TextField> hostFields=new List<TextField>();readonly List<Label> hostStates=new List<Label>();readonly List<Button> hostButtons=new List<Button>();
        bool connecting;string hostState="Not connected";
        void CreateHostControls(VisualElement parent,string name)
        {
            var group=new VisualElement{name=name};group.AddToClassList("host-controls");parent.Add(group);
            var field=new TextField("API host"){value=apiBase};field.AddToClassList("host-field");group.Add(field);hostFields.Add(field);
            var button=new Button(()=>StartCoroutine(ConnectHost(field.value))){text="Connect"};group.Add(button);hostButtons.Add(button);
            var state=new Label(hostState);state.AddToClassList("host-state");state.tooltip=hostState;group.Add(state);hostStates.Add(state);
        }
        public static bool TryNormalizeHost(string input,out string normalized)
        {
            normalized=null;if(string.IsNullOrWhiteSpace(input))return false;input=input.Trim();bool bare=!input.Contains("://");
            if(bare)input="http://"+input;
            if(!Uri.TryCreate(input,UriKind.Absolute,out var uri)||(uri.Scheme!="http"&&uri.Scheme!="https")||string.IsNullOrEmpty(uri.Host)||uri.UserInfo!=""||uri.AbsolutePath!="/"||uri.Query!=""||uri.Fragment!="")return false;
            if(bare&&uri.IsDefaultPort)uri=new UriBuilder(uri){Port=8000}.Uri;normalized=uri.GetLeftPart(UriPartial.Authority).TrimEnd('/');return true;
        }
        void HostStatus(string text,bool error=false)
        {
            hostState=text;foreach(var label in hostStates){label.text=text;label.tooltip=text;label.EnableInClassList("host-error",error);}foreach(var button in hostButtons)button.SetEnabled(!connecting);
        }
        public IEnumerator ConnectHost(string input)
        {
            if(connecting)yield break;
            if(!TryNormalizeHost(input,out string candidate)){HostStatus("Enter a valid host or HTTP(S) URL",true);yield break;}
            if(uploading||opening||downloading||deleting||tracking.items.Exists(ActiveUpload)){HostStatus("Finish active jobs before reconnecting",true);yield break;}
            connecting=true;HostStatus("Checking connection…");
            try
            {
                using(var request=UnityWebRequest.Get(candidate+"/api/v1/health"))
                {
                    request.timeout=8;yield return request.SendWebRequest();
                    if(request.result!=UnityWebRequest.Result.Success){HostStatus("Connection failed: "+request.error,true);yield break;}
                    ServerHealth health=null;try{health=JsonUtility.FromJson<ServerHealth>(request.downloadHandler.text);}catch{}
                    if(health==null||!health.ok||health.db!="ok"){HostStatus("API or database is not healthy",true);yield break;}
                    bool changed=candidate!=apiBase;apiBase=candidate;PlayerPrefs.SetString("Subsurface.ApiHost",apiBase);PlayerPrefs.Save();foreach(var field in hostFields)field.SetValueWithoutNotify(apiBase);
                    if(changed){revision++;openRevision++;tracking.items.Clear();selected.Clear();files.Clear();Render();ShowLibrary();search.SetValueWithoutNotify("");searchPlaceholder.style.display=DisplayStyle.Flex;searchClear.style.visibility=Visibility.Hidden;}
                    HostStatus(health.worker_alive?"Connected":"Connected · worker offline");Refresh();
                }
            }
            finally{connecting=false;foreach(var button in hostButtons)button.SetEnabled(true);}
        }
    }
}
