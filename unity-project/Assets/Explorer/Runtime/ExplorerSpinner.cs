using UnityEngine;
using UnityEngine.UIElements;

namespace Subsurface.Explorer
{
    public sealed class ExplorerSpinner : VisualElement
    {
        public float Angle {get;private set;}
        public ExplorerSpinner()
        {
            pickingMode=PickingMode.Ignore;AddToClassList("spinner");
            schedule.Execute(()=>{Angle=(Time.realtimeSinceStartup*240)%360;style.rotate=new Rotate(new UnityEngine.UIElements.Angle(Angle,AngleUnit.Degree));}).Every(30);
        }
    }
}
